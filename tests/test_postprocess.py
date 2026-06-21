"""Unit + adversarial tests for postprocess (parsing & rendering).

The model's raw output is untrusted text — these tests hammer the parser with
malformed, truncated, fenced, and weird-typed input, since a meeting pipeline
must never crash on a bad transcription.
"""
import json

import pytest

from tts_serve.postprocess import (
    Segment, _fmt_ts, _to_seconds, parse_segments, render, to_srt, to_transcript,
)


# ---------- _to_seconds ----------

@pytest.mark.parametrize("val,expected", [
    (0, 0.0),
    (12, 12.0),
    (12.5, 12.5),
    ("12.5", 12.5),
    ("00:00:10.500", 10.5),
    ("01:02:03", 3723.0),
    ("02:03", 123.0),
    ("", 0.0),
    ("garbage", 0.0),
    (None, 0.0),
])
def test_to_seconds(val, expected):
    assert _to_seconds(val) == expected


# ---------- _fmt_ts ----------

def test_fmt_ts_basic():
    assert _fmt_ts(0) == "00:00:00,000"
    assert _fmt_ts(3723.456) == "01:02:03,456"

def test_fmt_ts_negative_clamped():
    assert _fmt_ts(-5) == "00:00:00,000"

def test_fmt_ts_rounding():
    # 1.9995s rounds to 2000ms -> rolls into seconds
    assert _fmt_ts(1.9995) == "00:00:02,000"

def test_fmt_ts_vtt_sep():
    assert _fmt_ts(10.5, sep=".") == "00:00:10.500"


# ---------- parse_segments: happy paths ----------

def test_parse_vllm_style_keys():
    raw = json.dumps([{"Start": 1.0, "End": 2.0, "Speaker ID": 0, "Content": "hi"}])
    segs = parse_segments(raw)
    assert len(segs) == 1
    assert segs[0].speaker == "Speaker 0" and segs[0].text == "hi"

def test_parse_processor_style_keys():
    raw = json.dumps([{"start_time": 1.0, "end_time": 2.0, "speaker_id": 3, "text": "yo"}])
    segs = parse_segments(raw)
    assert segs[0].speaker == "Speaker 3"

def test_parse_chinese_unicode():
    raw = json.dumps([{"Start": 0, "End": 1, "Speaker ID": 1, "Content": "你好，世界"}],
                     ensure_ascii=False)
    segs = parse_segments(raw)
    assert segs[0].text == "你好，世界"


# ---------- parse_segments: ADVERSARIAL ----------

def test_parse_empty_string():
    assert parse_segments("") == []

def test_parse_whitespace_only():
    assert parse_segments("   \n  ") == []

def test_parse_not_json_at_all():
    assert parse_segments("the model said something conversational") == []

def test_parse_markdown_fenced():
    raw = "```json\n" + json.dumps([{"Start": 0, "End": 1, "Speaker ID": 0, "Content": "x"}]) + "\n```"
    assert len(parse_segments(raw)) == 1

def test_parse_truncated_array_recovers_objects():
    # stream cut off mid-array (no closing ]) — fallback regex should recover whole objects
    raw = '[{"Start":0,"End":1,"Speaker ID":0,"Content":"a"},{"Start":1,"End":2,"Speaker ID":1,"Content":"b"'
    segs = parse_segments(raw)
    assert len(segs) == 1  # only the first complete {...} object is recoverable
    assert segs[0].text == "a"

def test_parse_skips_empty_text_segments():
    raw = json.dumps([
        {"Start": 0, "End": 1, "Speaker ID": 0, "Content": ""},
        {"Start": 1, "End": 2, "Speaker ID": 0, "Content": "real"},
    ])
    segs = parse_segments(raw)
    assert len(segs) == 1 and segs[0].text == "real"

def test_parse_none_speaker_becomes_question_mark():
    raw = json.dumps([{"Start": 0, "End": 1, "Content": "[Music]"}])
    segs = parse_segments(raw)
    assert segs[0].speaker == "?"

def test_parse_non_numeric_speaker_label_preserved():
    raw = json.dumps([{"Start": 0, "End": 1, "Speaker ID": "Alice", "Content": "hi"}])
    assert parse_segments(raw)[0].speaker == "Alice"

def test_parse_object_not_list():
    # a single object, not wrapped in a list -> json.loads gives dict, not list -> no segments,
    # but fallback regex recovers the object
    raw = '{"Start":0,"End":1,"Speaker ID":0,"Content":"solo"}'
    segs = parse_segments(raw)
    assert len(segs) == 1 and segs[0].text == "solo"

def test_parse_string_timestamps():
    raw = json.dumps([{"Start": "00:01:00", "End": "00:01:05", "Speaker ID": 0, "Content": "t"}])
    segs = parse_segments(raw)
    assert segs[0].start == 60.0 and segs[0].end == 65.0

def test_parse_garbage_objects_skipped():
    raw = '[{"foo":"bar"}, {"Start":0,"End":1,"Speaker ID":0,"Content":"keep"}]'
    segs = parse_segments(raw)
    assert len(segs) == 1 and segs[0].text == "keep"


# ---------- SRT / transcript rendering ----------

def _segs():
    return [
        Segment(0.0, 2.0, "Speaker 0", "hello"),
        Segment(2.0, 4.0, "Speaker 0", "again"),
        Segment(4.0, 6.0, "Speaker 1", "hi there"),
    ]

def test_srt_structure():
    srt = to_srt(_segs())
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000\nSpeaker 0: hello")
    assert "3\n00:00:04,000 --> 00:00:06,000\nSpeaker 1: hi there" in srt

def test_transcript_merges_consecutive_speaker():
    txt = to_transcript(_segs())
    # consecutive Speaker 0 lines grouped under one header
    assert txt.count("Speaker 0:") == 1
    assert txt.count("Speaker 1:") == 1
    assert "hello" in txt and "again" in txt

def test_render_roundtrip_counts():
    raw = json.dumps([{"Start": 0, "End": 1, "Speaker ID": 0, "Content": "a"}])
    txt, srt, n = render(raw)
    assert n == 1 and "Speaker 0" in txt and "Speaker 0: a" in srt

def test_render_empty_is_safe():
    txt, srt, n = render("")
    assert n == 0 and srt == "" and txt.strip() == ""
