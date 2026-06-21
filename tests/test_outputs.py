"""Unit + adversarial tests for the canonical output layer."""
import json

import pytest

from tts_serve.outputs import (
    build_document, normalize_segments, render_format, write_outputs,
)


# ---------- normalize_segments ----------

def test_normalize_processor_keys():
    segs = normalize_segments([{"start_time": 1.0, "end_time": 2.0, "speaker_id": 0, "text": "hi"}])
    assert segs == [{"start": 1.0, "end": 2.0, "speaker": "Speaker 0", "text": "hi"}]

def test_normalize_vllm_keys():
    segs = normalize_segments([{"Start": 1, "End": 2, "Speaker ID": 2, "Content": "yo"}])
    assert segs[0]["speaker"] == "Speaker 2" and segs[0]["text"] == "yo"

def test_normalize_none_speaker():
    segs = normalize_segments([{"start_time": 0, "end_time": 1, "text": "[Music]"}])
    assert segs[0]["speaker"] == "?"

def test_normalize_string_digit_speaker():
    segs = normalize_segments([{"start_time": 0, "end_time": 1, "speaker_id": "1", "text": "x"}])
    assert segs[0]["speaker"] == "Speaker 1"

def test_normalize_named_speaker():
    segs = normalize_segments([{"start_time": 0, "end_time": 1, "speaker_id": "Bob", "text": "x"}])
    assert segs[0]["speaker"] == "Bob"

def test_normalize_missing_times_default_zero():
    segs = normalize_segments([{"speaker_id": 0, "text": "x"}])
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 0.0

def test_normalize_none_time_value():
    # a null time must not crash float()
    segs = normalize_segments([{"start_time": None, "end_time": 2.0, "speaker_id": 0, "text": "x"}])
    assert segs[0]["start"] == 0.0

def test_normalize_empty():
    assert normalize_segments([]) == []


# ---------- build_document ----------

def test_build_document_duration_and_speakers():
    segs = [
        {"start": 0.0, "end": 5.0, "speaker": "Speaker 0", "text": "a"},
        {"start": 5.0, "end": 12.5, "speaker": "Speaker 1", "text": "b"},
    ]
    doc = build_document(segs, source="file:x", model="m", meeting_name="mtg")
    assert doc["duration_s"] == 12.5
    assert doc["n_segments"] == 2
    assert doc["speakers"] == ["Speaker 0", "Speaker 1"]
    assert doc["source"] == "file:x"

def test_build_document_excludes_unknown_speaker():
    segs = [{"start": 0, "end": 1, "speaker": "?", "text": "[Music]"}]
    doc = build_document(segs, source="s", model="m")
    assert doc["speakers"] == []

def test_build_document_empty():
    doc = build_document([], source="s", model="m")
    assert doc["duration_s"] == 0.0 and doc["n_segments"] == 0 and doc["speakers"] == []

def test_build_document_extra_drops_none():
    doc = build_document([], source="s", model="m", hotwords=None, gen_seconds=3.2)
    assert "hotwords" not in doc and doc["gen_seconds"] == 3.2


# ---------- render_format ----------

def _doc():
    return build_document(
        [{"start": 0.0, "end": 2.0, "speaker": "Speaker 0", "text": "hello"}],
        source="s", model="m",
    )

def test_render_json_is_parseable_and_complete():
    out = render_format(_doc(), "json")
    parsed = json.loads(out)
    assert parsed["segments"][0]["text"] == "hello"

def test_render_txt_and_srt():
    assert "Speaker 0" in render_format(_doc(), "txt")
    assert "Speaker 0: hello" in render_format(_doc(), "srt")

def test_render_invalid_format_raises():
    with pytest.raises(ValueError):
        render_format(_doc(), "docx")


# ---------- write_outputs ----------

def test_write_outputs_creates_all_files(tmp_path):
    doc = _doc()
    paths = write_outputs(tmp_path / "mtg", doc)
    for key in ("segments.json", "transcript.txt", "subtitle.srt", "meta.json"):
        assert paths[key].exists(), key
    # meta.json must NOT contain the bulky segments list
    meta = json.loads(paths["meta.json"].read_text())
    assert "segments" not in meta and meta["n_segments"] == 1
    # segments.json must contain the segments
    assert json.loads(paths["segments.json"].read_text())["segments"][0]["text"] == "hello"

def test_write_outputs_unicode(tmp_path):
    doc = build_document(
        [{"start": 0, "end": 1, "speaker": "Speaker 0", "text": "你好 mixed 中英"}],
        source="s", model="m",
    )
    paths = write_outputs(tmp_path / "zh", doc)
    assert "你好" in paths["transcript.txt"].read_text(encoding="utf-8")
