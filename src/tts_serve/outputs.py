"""Canonical transcript output: normalize segments and write txt/srt/json.

Shared by the CLI (one source) and the folder pipeline. The canonical
machine format is ``segments.json`` with clean keys:
    {"start": float_seconds, "end": float_seconds, "speaker": "Speaker N", "text": str}
"""
from __future__ import annotations

import json
from pathlib import Path

from tts_serve.postprocess import render


def normalize_segments(raw_segments: list[dict]) -> list[dict]:
    """VibeVoice processor segments (start_time/end_time/speaker_id/text)
    -> canonical {start, end, speaker, text}."""
    out = []
    for s in raw_segments:
        spk = s.get("speaker_id", s.get("Speaker ID", s.get("Speaker")))
        speaker = (
            f"Speaker {spk}"
            if isinstance(spk, int) or (isinstance(spk, str) and spk.isdigit())
            else (str(spk) if spk is not None else "?")
        )
        out.append(
            {
                "start": float(s.get("start_time", s.get("Start", 0.0)) or 0.0),
                "end": float(s.get("end_time", s.get("End", 0.0)) or 0.0),
                "speaker": speaker,
                "text": str(s.get("text", s.get("Content", ""))).strip(),
            }
        )
    return out


def build_document(segments: list[dict], *, source: str, model: str,
                   meeting_name: str | None = None, **extra) -> dict:
    duration = max((s["end"] for s in segments), default=0.0)
    speakers = sorted({s["speaker"] for s in segments if s["speaker"] != "?"})
    doc = {
        "source": source,
        "meeting_name": meeting_name,
        "model": model,
        "duration_s": round(duration, 2),
        "n_segments": len(segments),
        "speakers": speakers,
        "segments": segments,
    }
    doc.update({k: v for k, v in extra.items() if v is not None})
    return doc


def render_format(doc: dict, fmt: str) -> str:
    """Render a built document to a single format string: json|txt|srt."""
    if fmt == "json":
        return json.dumps(doc, ensure_ascii=False, indent=2)
    # postprocess.render works off the canonical keys (start/end/speaker/text)
    txt, srt, _ = render(json.dumps(
        [{"Start": s["start"], "End": s["end"], "Speaker ID": s["speaker"], "Content": s["text"]}
         for s in doc["segments"]]
    ))
    if fmt == "txt":
        return txt
    if fmt == "srt":
        return srt
    raise ValueError(f"unknown format: {fmt}")


def write_outputs(out_dir: Path, doc: dict) -> dict[str, Path]:
    """Write transcript.txt, subtitle.srt, segments.json, meta.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    (out_dir / "segments.json").write_text(render_format(doc, "json"), encoding="utf-8")
    (out_dir / "transcript.txt").write_text(render_format(doc, "txt"), encoding="utf-8")
    (out_dir / "subtitle.srt").write_text(render_format(doc, "srt"), encoding="utf-8")
    meta = {k: v for k, v in doc.items() if k != "segments"}
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in ("segments.json", "transcript.txt", "subtitle.srt", "meta.json"):
        paths[name] = out_dir / name
    return paths
