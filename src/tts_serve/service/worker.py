"""Resident GPU worker: loads VibeVoice-ASR once, drains the task queue serially.

Single worker => GPU concurrency = 1 (the model is the bottleneck). Polls SQLite
for queued tasks, transcribes via the shared core pipeline (with stage progress
written back to the store), writes artifacts to <data>/tasks/<id>/results/.
"""
from __future__ import annotations

import os

# Reduce cross-file GPU fragmentation (must precede torch import).
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import sys
import time
import traceback

from tts_serve import core
from tts_serve.outputs import write_outputs
from tts_serve.service import store
from tts_serve.sources import SourceOpts


def _default_cookies() -> str | None:
    """Use YT_COOKIES if set, else data/bili_cookies.txt (from scripts/bili_login.py)
    so Bilibili works out of the box once logged in."""
    env = os.environ.get("YT_COOKIES")
    if env:
        return env
    default = store.DATA / "bili_cookies.txt"
    return str(default) if default.exists() else None


def _opts_from_env() -> SourceOpts:
    return SourceOpts(
        aws_profile=os.environ.get("AWS_PROFILE"),
        aws_region=os.environ.get("AWS_REGION"),
        gdrive_credentials=os.environ.get("GDRIVE_CREDENTIALS"),
        cookies=_default_cookies(),
    )


def _process(asr, task: dict) -> None:
    tid = task["id"]
    o = task["options"]
    src_opts = _opts_from_env()
    if o.get("gdrive_public"):
        src_opts.gdrive_public = True
    doc = core.transcribe_source(
        task["source"], workdir=store.task_dir(tid), opts=src_opts, asr=asr,
        hotwords=o.get("hotwords"), speakers=o.get("speakers"),
        reid=bool(o.get("reid")), names=bool(o.get("names")),
        clip=o.get("clip"), name=o.get("name"),
        progress=lambda st: store.update(tid, stage=st),
    )
    write_outputs(store.results_dir(tid), doc)
    store.update(tid, status="done", stage="done")
    print(f"[worker] {tid} done: {doc['n_segments']} segs, speakers={doc['speakers']}", flush=True)


def main() -> None:
    store.init()
    reclaimed = store.reclaim_stale()  # requeue tasks orphaned by a previous crash
    if reclaimed:
        print(f"[worker] re-queued {reclaimed} stale running task(s)", flush=True)
    print("[worker] loading VibeVoice-ASR (resident)...", flush=True)
    from tts_serve.asr import VibeVoiceASR
    asr = VibeVoiceASR()
    print(f"[worker] model ready in {asr.load_seconds:.1f}s; polling queue", flush=True)
    poll = float(os.environ.get("TTS_SERVE_POLL", "1.0"))
    retention = float(os.environ.get("TTS_SERVE_RETENTION_DAYS", "7"))
    last_maint = 0.0
    while True:
        # lifecycle maintenance roughly hourly (purge old terminal tasks + WAL checkpoint)
        if time.time() - last_maint > 3600:
            n = store.purge_old(retention)
            if n:
                print(f"[worker] purged {n} old task(s)", flush=True)
            last_maint = time.time()
        task = store.claim_next_queued()
        if not task:
            time.sleep(poll)
            continue
        print(f"[worker] claimed {task['id']} ({task['source_type']})", flush=True)
        try:
            _process(asr, task)
        except Exception as e:  # noqa: BLE001
            store.update(task["id"], status="failed", error=str(e))
            print(f"[worker] {task['id']} FAILED: {e}", file=sys.stderr, flush=True)
            traceback.print_exc()


if __name__ == "__main__":
    main()
