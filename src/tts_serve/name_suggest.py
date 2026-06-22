"""LLM-based speaker name suggestion from self-introductions (post-processing).

Speakers often self-introduce ("I'm Skyler, I've been an investor at Pelion...").
This sends each speaker's early segments to an LLM (DeepSeek) and asks for the
speaker's OWN stated name.

No regex/keyword heuristics: pattern matching has too many false positives — e.g.
"this is Elizabeth Warren" (talking ABOUT someone) or "I'm good" look like intros
but aren't. The LLM reliably distinguishes a genuine self-introduction from a
third-person mention.

Output: {speaker: {name, confidence, evidence, method:"llm"}} — *suggestions*; the
caller decides whether to apply them (don't silently rename).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

_EARLY_S = 240.0   # intros usually happen early
_FIRST_N = 6       # ...or in a speaker's first few segments
_MODEL = "deepseek-chat"

_PROMPT = (
    "Below is a transcript excerpt from ONE speaker in a meeting. Determine whether "
    "this speaker introduces THEMSELVES by name (a genuine self-introduction such as "
    "\"I'm Alex\" / \"my name is Alex\" / \"this is Alex speaking\"). Do NOT return a "
    "name that the speaker only mentions about someone else (e.g. \"this is Elizabeth "
    "Warren\" when discussing her, or \"thanks, Alex\"). If there is no clear "
    "self-introduction, return null.\n\n"
    "Reply with STRICT JSON only: "
    "{\"name\": string|null, \"confidence\": number 0..1, \"evidence\": short quote|null}\n\n"
    "Excerpt:\n"
)


def _speaker_early_text(segments: list[dict]) -> dict[str, str]:
    by_spk: dict[str, list[dict]] = defaultdict(list)
    for s in segments:
        if s.get("speaker") in (None, "?"):
            continue
        by_spk[s["speaker"]].append(s)
    out = {}
    for spk, segs in by_spk.items():
        early = [s for s in segs if s["start"] < _EARLY_S] or segs[:_FIRST_N]
        early = (early + segs[:_FIRST_N])[:8]
        out[spk] = " ".join(dict.fromkeys(s["text"] for s in early))
    return out


def _deepseek(prompt: str, api_key: str, model: str = _MODEL) -> str:
    import requests
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "temperature": 0,
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 200, "response_format": {"type": "json_object"}},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def suggest_names(segments: list[dict], api_key: str | None = None,
                  model: str = _MODEL) -> dict[str, dict]:
    """Ask the LLM for each speaker's self-stated name. Returns {} if no API key
    (DeepSeek is required; we don't fall back to regex). Sends only each speaker's
    early segments."""
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return {}
    out = {}
    for spk, text in _speaker_early_text(segments).items():
        if not text.strip():
            continue
        try:
            data = json.loads(_deepseek(_PROMPT + text[:1500], api_key, model))
        except Exception:  # noqa: BLE001
            continue
        if data.get("name"):
            out[spk] = {"name": str(data["name"]).strip(),
                        "confidence": float(data.get("confidence", 0.5)),
                        "evidence": data.get("evidence"), "method": "llm"}
    return out


if __name__ == "__main__":
    import sys
    doc = json.load(open(sys.argv[1]))
    segs = doc["segments"] if isinstance(doc, dict) else doc
    print(json.dumps(suggest_names(segs), ensure_ascii=False, indent=2))
