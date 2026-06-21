"""Convert VibeVoice-ASR JSON segments into readable transcript + SRT.

The vLLM plugin returns a JSON array of segments shaped like:
    [{"Start": 2.99, "End": 5.30, "Speaker ID": 0, "Content": "..."}, ...]

Times are in seconds (floats). Keys are tolerated in a few spellings because
the model occasionally varies them ("Start"/"Start time", etc.).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class Segment:
    start: float
    end: float
    speaker: str
    text: str


_START_KEYS = ("Start", "Start time", "start", "start_time")
_END_KEYS = ("End", "End time", "end", "end_time")
_SPK_KEYS = ("Speaker ID", "Speaker", "speaker", "speaker_id")
_TXT_KEYS = ("Content", "Text", "content", "text")


def _first(d: dict, keys: tuple[str, ...], default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def _to_seconds(v) -> float:
    """Accept floats, ints, or "HH:MM:SS.mmm" / "MM:SS" strings."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        v = v.strip()
        if ":" in v:
            parts = [float(p) for p in v.split(":")]
            sec = 0.0
            for p in parts:
                sec = sec * 60 + p
            return sec
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def parse_segments(raw: str) -> list[Segment]:
    """Parse the model's raw streamed output into Segments.

    Tolerates the array being wrapped in markdown fences or missing its
    closing bracket (truncated stream): we extract the {...} objects.
    """
    text = raw.strip()
    # Strip ```json fences if present.
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    objs: list[dict] = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            objs = [o for o in data if isinstance(o, dict)]
        elif isinstance(data, dict):
            objs = [data]
    except json.JSONDecodeError:
        # Fallback: pull out individual {...} blocks and parse each.
        for m in re.finditer(r"\{[^{}]*\}", text):
            try:
                objs.append(json.loads(m.group(0)))
            except json.JSONDecodeError:
                continue

    segments: list[Segment] = []
    for o in objs:
        spk = _first(o, _SPK_KEYS, default="?")
        seg = Segment(
            start=_to_seconds(_first(o, _START_KEYS, 0.0)),
            end=_to_seconds(_first(o, _END_KEYS, 0.0)),
            speaker=f"Speaker {spk}" if isinstance(spk, int) or (isinstance(spk, str) and spk.isdigit()) else str(spk),
            text=str(_first(o, _TXT_KEYS, "")).strip(),
        )
        if seg.text:
            segments.append(seg)
    return segments


def _fmt_ts(seconds: float, sep: str = ",") -> str:
    """Format seconds as SRT/VTT timestamp HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}")
        lines.append(f"{seg.speaker}: {seg.text}")
        lines.append("")
    return "\n".join(lines)


def to_transcript(segments: list[Segment]) -> str:
    """Readable transcript: '[MM:SS] Speaker N: text', merging consecutive
    lines from the same speaker into a paragraph."""
    lines: list[str] = []
    prev_spk = None
    for seg in segments:
        ts = _fmt_ts(seg.start, sep=".")[:-4]  # HH:MM:SS
        if seg.speaker != prev_spk:
            lines.append("")
            lines.append(f"[{ts}] {seg.speaker}:")
            prev_spk = seg.speaker
        lines.append(f"  {seg.text}")
    return "\n".join(lines).strip() + "\n"


def render(raw: str) -> tuple[str, str, int]:
    """Return (transcript_txt, srt, n_segments) from raw model output."""
    segs = parse_segments(raw)
    return to_transcript(segs), to_srt(segs), len(segs)


if __name__ == "__main__":
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    with open(src, encoding="utf-8") as f:
        raw = f.read()
    txt, srt, n = render(raw)
    print(f"# parsed {n} segments\n", file=sys.stderr)
    out_base = sys.argv[2] if len(sys.argv) > 2 else None
    if out_base:
        with open(out_base + ".txt", "w", encoding="utf-8") as f:
            f.write(txt)
        with open(out_base + ".srt", "w", encoding="utf-8") as f:
            f.write(srt)
        print(f"wrote {out_base}.txt and {out_base}.srt", file=sys.stderr)
    else:
        print(txt)
