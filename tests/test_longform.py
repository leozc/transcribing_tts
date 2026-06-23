"""Long-form chunking: the server splits audio too long for a single GPU pass into
chunks, transcribes each with the resident model, offsets timestamps to absolute time,
and merges into ONE unified document. No GPU — the ASR + ffmpeg are faked."""
from pathlib import Path

from tts_serve import core


class _FakeRes:
    # each chunk reports two segments at chunk-relative t=10s and t=100s
    def __init__(self):
        self.segments = [
            {"start": 10.0, "end": 20.0, "speaker": "Speaker 0", "text": "hello"},
            {"start": 100.0, "end": 110.0, "speaker": "Speaker 1", "text": "world"},
        ]
        self.gen_seconds = 5.0
        self.peak_vram_gb = 20.0


class _FakeASR:
    def __init__(self):
        self.calls = 0
    def transcribe(self, wav, **kw):
        self.calls += 1
        return _FakeRes()


def test_chunked_merge_offsets_timestamps(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "_free_cache", lambda: None)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: None)        # skip ffmpeg
    monkeypatch.setattr(core, "normalize_segments", lambda segs: [dict(s) for s in segs])

    asr = _FakeASR()
    # 2500s audio, 820s chunks -> starts at 0, 820, 1640, 2460 = 4 chunks
    segs, gen, peak = core._transcribe_chunked(
        asr, tmp_path / "audio.wav", total=2500.0, chunk_seconds=820.0,
        max_new_tokens=16384, hw=None, workdir=tmp_path, p=lambda s: None)

    assert asr.calls == 4                                  # one pass per chunk
    assert len(segs) == 8                                  # 2 segments * 4 chunks
    # timestamps offset to absolute time (chunk i starts at i*820)
    assert sorted(s["start"] for s in segs) == [10, 100, 830, 920, 1650, 1740, 2470, 2560]
    assert gen == 20.0 and peak == 20.0                    # gen summed, peak maxed


def test_transcribe_source_routes_by_duration(monkeypatch, tmp_path):
    # short audio -> single pass; long audio -> chunked. Mock everything GPU/IO.
    monkeypatch.setattr(core, "resolve", lambda *a, **k: type("R", (), {
        "local_path": tmp_path / "in.mp4", "label": "x", "name": "x"})())
    monkeypatch.setattr(core, "clip_and_normalize", lambda *a, **k: None)
    monkeypatch.setattr(core, "_free_cache", lambda: None)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(core, "normalize_segments", lambda segs: [dict(s) for s in segs])

    asr = _FakeASR()
    # short: 300s < 820s chunk -> single pass (1 call), chunked False
    monkeypatch.setattr(core, "_duration", lambda w: 300.0)
    doc = core.transcribe_source("u://x", workdir=tmp_path, asr=asr, chunk_seconds=820)
    assert asr.calls == 1 and doc.get("chunked") is False

    # long: 2000s > 820s -> chunked (3 calls: 0/820/1640), chunked True
    asr.calls = 0
    monkeypatch.setattr(core, "_duration", lambda w: 2000.0)
    doc = core.transcribe_source("u://x", workdir=tmp_path, asr=asr, chunk_seconds=820)
    assert asr.calls == 3 and doc.get("chunked") is True and doc.get("chunk_seconds") == 820.0


def test_chunk_threshold_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_SERVE_CHUNK_SECONDS", "100")
    monkeypatch.setattr(core, "resolve", lambda *a, **k: type("R", (), {
        "local_path": tmp_path / "in.mp4", "label": "x", "name": "x"})())
    monkeypatch.setattr(core, "clip_and_normalize", lambda *a, **k: None)
    monkeypatch.setattr(core, "_free_cache", lambda: None)
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(core, "normalize_segments", lambda segs: [dict(s) for s in segs])
    monkeypatch.setattr(core, "_duration", lambda w: 250.0)  # 250s > 100s -> chunk into 3
    asr = _FakeASR()
    doc = core.transcribe_source("u://x", workdir=tmp_path, asr=asr)
    assert asr.calls == 3 and doc["chunk_seconds"] == 100.0
