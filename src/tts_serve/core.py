"""Shared transcription pipeline: a SOURCE -> a canonical document.

Used by both the CLI (`cli.cmd_transcribe`) and the service worker, so there's one
code path: resolve source -> 16kHz mono -> ASR -> optional voiceprint re-id ->
optional LLM speaker names -> build_document.

Pass a resident ``asr`` (loaded once) to avoid reloading the 17GB model per call;
omit it and one is constructed. ``progress(stage)`` reports stage transitions
(downloading / preprocessing / transcribing / postprocessing).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from tts_serve.outputs import build_document, normalize_segments
from tts_serve.sources import SourceOpts, resolve

DEFAULT_MODEL = "microsoft/VibeVoice-ASR"


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


def transcribe_source(
    source: str, *, workdir, opts: SourceOpts | None = None, asr=None,
    hotwords: str | None = None, speakers: int | None = None,
    reid: bool = False, names: bool = False, clip: str | None = None,
    model: str = DEFAULT_MODEL, max_new_tokens: int = 16384,
    name: str | None = None, progress: Callable[[str], None] | None = None,
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
    res = asr.transcribe(str(wav), max_new_tokens=max_new_tokens, hotwords=hw)
    segments = normalize_segments(res.segments)

    suggested_names = None
    if reid or names:
        _p("postprocessing")
    if reid:
        from tts_serve.diarize import SpeakerReID
        segments = SpeakerReID().relabel(str(wav), segments, n_speakers=speakers)
    if names:
        from tts_serve.name_suggest import suggest_names
        suggested_names = suggest_names(segments) or None

    return build_document(
        segments, source=rs.label, model=model,
        meeting_name=name or rs.name, speaker_names=suggested_names,
        gen_seconds=round(res.gen_seconds, 1),
        peak_vram_gb=round(res.peak_vram_gb, 2),
    )
