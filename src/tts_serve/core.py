"""Shared transcription pipeline: a SOURCE -> a canonical document.

Used by both the CLI (`cli.cmd_transcribe`) and the service worker, so there's one
code path: resolve source -> 16kHz mono -> ASR -> optional voiceprint re-id ->
optional LLM speaker names -> build_document.

Pass a resident ``asr`` (loaded once) to avoid reloading the 17GB model per call;
omit it and one is constructed. ``progress(stage)`` reports stage transitions
(downloading / preprocessing / transcribing / postprocessing).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from tts_serve.outputs import build_document, normalize_segments
from tts_serve.sources import SourceOpts, resolve

DEFAULT_MODEL = "microsoft/VibeVoice-ASR"
# A single forward pass over long audio OOMs a 24GB card (~21GB at 20min); chunk
# anything longer than this window. Each ~13.7min chunk peaks ~20GB. Env-overridable.
DEFAULT_CHUNK_SECONDS = 820.0


def clip_and_normalize(src, dst, clip: str | None = None) -> None:
    """ffmpeg -> 16kHz mono WAV, optionally clipping START-END (seconds)."""
    cmd = ["ffmpeg", "-y"]
    if clip:
        start, _, end = clip.partition("-")
        if start:
            cmd += ["-ss", start]
        if end:
            cmd += ["-to", end]
    cmd += ["-i", str(src), "-ar", "16000", "-ac", "1", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)


def _duration(wav) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(wav)])
    return float(out)


def _free_cache() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — torch absent on a no-GPU dev box
        pass


def _transcribe_chunked(asr, wav, total, chunk_seconds, max_new_tokens, hw, workdir, p):
    """Transcribe long audio chunk-by-chunk (avoids the single-pass OOM), offsetting
    each chunk's timestamps to absolute time and concatenating. The resident model is
    reused; GPU cache is freed between chunks. Returns (segments, gen_seconds, peak_gb).

    Note: speaker ids are assigned per chunk and do NOT correspond across boundaries —
    pass reid=True to re-cluster speakers globally over the full audio afterwards."""
    starts = [i * chunk_seconds for i in range(int(total // chunk_seconds) + 1)
              if i * chunk_seconds < total]
    n = len(starts)
    all_segments: list[dict] = []
    gen_total, peak = 0.0, 0.0
    for i, start in enumerate(starts):
        p(f"transcribing chunk {i + 1}/{n}")
        cw = Path(workdir) / f"chunk_{i:02d}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-t", str(chunk_seconds),
             "-i", str(wav), "-ar", "16000", "-ac", "1", str(cw)],
            check=True, capture_output=True)
        res = asr.transcribe(str(cw), max_new_tokens=max_new_tokens, hotwords=hw)
        segs = normalize_segments(res.segments)
        for s in segs:  # chunk-relative -> absolute time
            s["start"] += start
            s["end"] += start
        all_segments.extend(segs)
        gen_total += res.gen_seconds
        peak = max(peak, res.peak_vram_gb)
        cw.unlink(missing_ok=True)
        _free_cache()
    return all_segments, gen_total, peak


def transcribe_source(
    source: str, *, workdir, opts: SourceOpts | None = None, asr=None,
    hotwords: str | None = None, speakers: int | None = None,
    reid: bool = False, names: bool = False, clip: str | None = None,
    model: str = DEFAULT_MODEL, max_new_tokens: int = 16384,
    name: str | None = None, chunk_seconds: float | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    opts = opts or SourceOpts()

    def _p(stage: str) -> None:
        if progress:
            progress(stage)

    _p("downloading")
    rs = resolve(source, workdir, opts)

    _p("preprocessing")
    wav = workdir / "audio_16k.wav"
    clip_and_normalize(rs.local_path, wav, clip)

    hw = hotwords
    if speakers:  # the model takes free-text context_info; fold the count in
        hint = f"There are {speakers} speakers."
        hw = f"{hw}. {hint}" if hw else hint

    if asr is None:
        from tts_serve.asr import VibeVoiceASR
        asr = VibeVoiceASR(model=model)

    _p("transcribing")
    cs = chunk_seconds or float(os.environ.get("TTS_SERVE_CHUNK_SECONDS", DEFAULT_CHUNK_SECONDS))
    total = _duration(wav)
    chunked = total > cs
    if chunked:  # long audio: chunk to avoid the single-pass OOM, then merge
        segments, gen_seconds, peak_vram = _transcribe_chunked(
            asr, wav, total, cs, max_new_tokens, hw, workdir, _p)
    else:
        res = asr.transcribe(str(wav), max_new_tokens=max_new_tokens, hotwords=hw)
        segments = normalize_segments(res.segments)
        gen_seconds, peak_vram = res.gen_seconds, res.peak_vram_gb

    suggested_names = None
    if reid or names:
        _p("postprocessing")
    if reid:  # global re-clustering also unifies chunk-local speaker ids across chunks
        from tts_serve.diarize import SpeakerReID
        segments = SpeakerReID().relabel(str(wav), segments, n_speakers=speakers)
    if names:
        from tts_serve.name_suggest import suggest_names
        suggested_names = suggest_names(segments) or None

    return build_document(
        segments, source=rs.label, model=model,
        meeting_name=name or rs.name, speaker_names=suggested_names,
        gen_seconds=round(gen_seconds, 1), peak_vram_gb=round(peak_vram, 2),
        chunked=chunked, chunk_seconds=(cs if chunked else None),
    )
