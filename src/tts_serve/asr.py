"""VibeVoice-ASR backend — loads the model once and transcribes audio.

This is the resident GPU worker: instantiate ``VibeVoiceASR`` once (it loads
~17GB of weights), then call ``transcribe()`` per file. Generation is serial
on the single GPU, matching the plan's gpu-concurrency=1 constraint.

vLLM serving was the original plan, but the host driver (535 / CUDA 12.2) is
incompatible with the vLLM image's CUDA 12.9 build, so we run via transformers
+ torch cu121 natively. Swap this class for an HTTP client if the driver is
upgraded and vLLM serving becomes available.
"""
from __future__ import annotations

import gc
import time
from dataclasses import dataclass

import torch

from vibevoice.modular.modeling_vibevoice_asr import (
    VibeVoiceASRForConditionalGeneration,
)
from vibevoice.processor.vibevoice_asr_processor import VibeVoiceASRProcessor

DEFAULT_MODEL = "microsoft/VibeVoice-ASR"


@dataclass
class TranscribeResult:
    raw_text: str
    segments: list[dict]  # keys: start_time, end_time, speaker_id, text
    gen_seconds: float
    out_tokens: int
    peak_vram_gb: float


class VibeVoiceASR:
    def __init__(self, model: str = DEFAULT_MODEL, device: str = "cuda"):
        self.device = device
        t0 = time.time()
        self.processor = VibeVoiceASRProcessor.from_pretrained(
            model, language_model_pretrained_name="Qwen/Qwen2.5-7B"
        )
        self.model = VibeVoiceASRForConditionalGeneration.from_pretrained(
            model,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            trust_remote_code=True,
        ).to(device)
        self.model.eval()
        self.load_seconds = time.time() - t0

    def transcribe(
        self, audio_path: str, max_new_tokens: int = 16384, hotwords: str | None = None
    ) -> TranscribeResult:
        """Transcribe one audio file. ``hotwords`` is a comma-separated string
        of names/terms injected into the prompt to bias recognition."""
        # The processor embeds hotwords/metadata into the prompt via context_info:
        # "...with extra info: {context_info}\n\nPlease transcribe it with these keys:..."
        context_info = hotwords.strip() if (hotwords and hotwords.strip()) else None
        inputs = self.processor(
            audio=[audio_path],
            sampling_rate=None,
            return_tensors="pt",
            padding=True,
            add_generation_prompt=True,
            context_info=context_info,
        )
        inputs = {
            k: (v.to(self.device) if isinstance(v, torch.Tensor) else v)
            for k, v in inputs.items()
        }
        n_in = inputs["input_ids"].shape[1]

        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=self.processor.pad_id,
                eos_token_id=self.processor.tokenizer.eos_token_id,
            )
        gen_s = time.time() - t0

        gen_ids = out[0, n_in:]
        eos = (gen_ids == self.processor.tokenizer.eos_token_id).nonzero(as_tuple=True)[0]
        if len(eos) > 0:
            gen_ids = gen_ids[: eos[0] + 1]
        raw = self.processor.decode(gen_ids, skip_special_tokens=True)
        try:
            segs = self.processor.post_process_transcription(raw)
        except Exception:  # noqa: BLE001
            segs = []

        n_out = int(gen_ids.shape[0])
        peak = (
            torch.cuda.max_memory_allocated() / 1e9 if self.device == "cuda" else 0.0
        )
        # Free this file's activations/KV before the next file, else the
        # reserved-but-unallocated cache fragments the GPU and the next
        # generate() OOMs on a 24GB card (weights alone are ~17.4GB).
        if self.device == "cuda":
            del out, inputs, gen_ids
            gc.collect()
            torch.cuda.empty_cache()
        return TranscribeResult(
            raw_text=raw,
            segments=segs,
            gen_seconds=gen_s,
            out_tokens=n_out,
            peak_vram_gb=peak,
        )
