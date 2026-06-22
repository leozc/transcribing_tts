"""Speaker re-identification: fix diarization speaker ids with voice fingerprints.

VibeVoice assigns speaker ids per pass, so they (a) over-count and (b) don't
align across chunks in long-form. This module embeds each segment's audio with a
speaker-embedding model (ECAPA-TDNN; CAM++ is a drop-in alternative) and
re-clusters across ALL segments — including across chunk boundaries — to produce
globally consistent speaker ids.

    reid = SpeakerReID()
    segments = reid.relabel(wav_16k, segments, n_speakers=2)  # or n_speakers=None to auto

``segments`` are canonical dicts: {start, end, speaker, text} (seconds).
"""
from __future__ import annotations

import numpy as np

DEFAULT_MODEL = "speechbrain/spkrec-ecapa-voxceleb"
MIN_SEG_S = 0.4   # pad shorter segments before embedding


class SpeakerReID:
    def __init__(self, model: str = DEFAULT_MODEL, device: str = "cuda"):
        import torch  # noqa: F401
        from speechbrain.inference.speaker import EncoderClassifier
        self._dev = device
        self.encoder = EncoderClassifier.from_hparams(
            source=model, run_opts={"device": device}
        )

    def embed(self, wav_path: str, segments: list[dict], sr: int = 16000) -> np.ndarray:
        """Return an (N, D) array of speaker embeddings, one per segment."""
        import torch
        import torchaudio
        sig, file_sr = torchaudio.load(wav_path)
        if file_sr != sr:
            sig = torchaudio.functional.resample(sig, file_sr, sr)
        if sig.shape[0] > 1:  # to mono
            sig = sig.mean(0, keepdim=True)
        total = sig.shape[1]
        min_len = int(MIN_SEG_S * sr)
        embs = []
        for s in segments:
            a = max(0, int(s["start"] * sr))
            b = min(total, int(s["end"] * sr))
            if b <= a:
                b = min(total, a + min_len)
            chunk = sig[:, a:b]
            if chunk.shape[1] < min_len:  # pad short segments by tiling
                reps = (min_len // max(1, chunk.shape[1])) + 1
                chunk = chunk.repeat(1, reps)[:, :min_len]
            with torch.no_grad():
                e = self.encoder.encode_batch(chunk).squeeze().detach().cpu().numpy()
            embs.append(e)
        return np.vstack(embs) if embs else np.empty((0, 192))

    @staticmethod
    def cluster(embs: np.ndarray, n_speakers: int | None = None,
                threshold: float = 0.25) -> np.ndarray:
        """Cluster embeddings -> integer label per segment.

        n_speakers: fix the count; None -> auto via cosine distance threshold.
        """
        from sklearn.cluster import AgglomerativeClustering
        n = embs.shape[0]
        if n == 0:
            return np.array([], dtype=int)
        if n == 1:
            return np.zeros(1, dtype=int)
        if n_speakers and n_speakers >= 1:
            k = min(n_speakers, n)
            if k == 1:
                return np.zeros(n, dtype=int)
            model = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average")
        else:
            model = AgglomerativeClustering(
                n_clusters=None, distance_threshold=threshold,
                metric="cosine", linkage="average")
        return model.fit_predict(embs)

    @staticmethod
    def _renumber_by_time(segments: list[dict], labels: np.ndarray) -> np.ndarray:
        """Renumber cluster ids so Speaker 0 is whoever speaks first, etc."""
        order, seen = {}, 0
        out = labels.copy()
        for i, lab in enumerate(labels):
            if lab not in order:
                order[lab] = seen
                seen += 1
        return np.array([order[l] for l in labels])

    @staticmethod
    def _is_speech(seg: dict, min_s: float = 0.6) -> bool:
        """Cluster only real speech: skip null-speaker, very short, and
        bracketed non-speech ([Music], [Applause], ...) — they're more distinct
        than two speakers and hijack the top-level split."""
        if seg.get("speaker") in (None, "?"):
            return False
        if (seg["end"] - seg["start"]) < min_s:
            return False
        t = seg.get("text", "").strip()
        if not t or (t.startswith("[") and t.endswith("]")):
            return False
        return True

    def relabel(self, wav_path: str, segments: list[dict],
                n_speakers: int | None = None, threshold: float = 0.45) -> list[dict]:
        """Re-assign segment['speaker'] using global voiceprint clustering.

        Non-speech / too-short segments are excluded from clustering and then
        inherit the nearest preceding speech speaker.
        """
        if len(segments) <= 1:
            return segments
        idx = [i for i, s in enumerate(segments) if self._is_speech(s)]
        if len(idx) <= 1:
            return segments
        sub = [segments[i] for i in idx]
        embs = self.embed(wav_path, sub)
        labels = self.cluster(embs, n_speakers=n_speakers, threshold=threshold)
        labels = self._renumber_by_time(sub, labels)
        assigned = {i: int(lab) for i, lab in zip(idx, labels)}
        last = 0
        for i, s in enumerate(segments):
            if i in assigned:
                last = assigned[i]
                s["speaker"] = f"Speaker {last}"
            else:  # non-speech inherits nearest preceding speaker
                s["speaker"] = f"Speaker {last}"
        return segments
