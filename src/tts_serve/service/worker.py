"""Resident GPU worker: loads VibeVoice-ASR once, drains the task queue serially.

Single worker => GPU concurrency = 1 (the model is the bottleneck). Polls SQLite
for queued tasks, transcribes via the shared core pipeline (with stage progress
written back to the store), writes artifacts to <data>/tasks/<id>/results/.
"""
from __future__ import annotations

import os

# Reduce cross-file GPU fragmentation (must precede torch import).
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import time

from tts_serve import core
from tts_serve.outputs import write_outputs
from tts_serve.service import store
from tts_serve.service.logconf import setup_logging
from tts_serve.sources import SourceOpts

log = setup_logging("worker")


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

    def progress(st: str) -> None:
        store.update(tid, stage=st)
        log.info("task %s stage=%s", tid, st)

    doc = core.transcribe_source(
        task["source"], workdir=store.task_dir(tid), opts=src_opts, asr=asr,
        hotwords=o.get("hotwords"), speakers=o.get("speakers"),
        reid=bool(o.get("reid")), names=bool(o.get("names")),
        clip=o.get("clip"), name=o.get("name"),
        progress=progress,
    )
    write_outputs(store.results_dir(tid), doc)
    store.update(tid, status="done", stage="done")
    log.info("task %s DONE: %d segs, speakers=%s", tid, doc["n_segments"], doc["speakers"])


def _load_model():
    from tts_serve.asr import VibeVoiceASR
    asr = VibeVoiceASR()
    log.info("model loaded in %.1fs", asr.load_seconds)
    return asr


def _free_gpu() -> None:
    """Release the model's GPU memory back to the driver. The ~17GB of weights are
    freed; a small CUDA context (~hundreds of MB) remains until the process exits."""
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:  # noqa: BLE001 — torch may be absent in a no-GPU dev box
        pass


def main() -> None:
    store.init()
    idle_unload = float(os.environ.get("TTS_SERVE_IDLE_UNLOAD", "0"))  # 0 = resident
    poll = float(os.environ.get("TTS_SERVE_POLL", "1.0"))
    retention = float(os.environ.get("TTS_SERVE_RETENTION_DAYS", "7"))
    mode = f"on-demand (free GPU after {idle_unload:.0f}s idle)" if idle_unload > 0 else "resident"
    log.info("worker starting | data=%s db=%s | model=%s", store.DATA, store.DB, mode)
    reclaimed = store.reclaim_stale()  # requeue tasks orphaned by a previous crash
    if reclaimed:
        log.warning("re-queued %d stale running task(s) from a previous crash", reclaimed)

    asr = None
    if idle_unload <= 0:
        asr = _load_model()  # resident: keep the model hot from startup
    else:
        log.info("model loads on first task; GPU stays free while the queue is empty")
    log.info("polling queue")

    last_maint = 0.0
    last_active = time.monotonic()
    while True:
        # lifecycle maintenance roughly hourly (purge old terminal tasks + WAL checkpoint)
        if time.time() - last_maint > 3600:
            n = store.purge_old(retention)
            if n:
                log.info("purged %d old task(s) (retention=%.0fd)", n, retention)
            last_maint = time.time()

        task = store.claim_next_queued()
        if not task:
            # on-demand: free the GPU once we've been idle past the threshold
            idle_for = time.monotonic() - last_active
            if asr is not None and idle_unload > 0 and idle_for > idle_unload:
                log.info("idle %.0fs > %.0fs: unloading model to free the GPU", idle_for, idle_unload)
                asr = None
                _free_gpu()
                log.info("model unloaded; GPU memory released (CUDA context may persist)")
            time.sleep(poll)
            continue

        log.info("claimed %s (client=%s type=%s)", task["id"], task.get("client_id"), task["source_type"])
        t0 = time.monotonic()
        try:
            if asr is None:  # on-demand cold start: load before processing
                store.update(task["id"], stage="loading_model")
                log.info("loading model on demand for task %s ...", task["id"])
                asr = _load_model()
            _process(asr, task)
            log.info("task %s finished in %.1fs", task["id"], time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001
            store.update(task["id"], status="failed", error=str(e))
            log.error("task %s FAILED after %.1fs: %s", task["id"], time.monotonic() - t0, e, exc_info=True)
        last_active = time.monotonic()


if __name__ == "__main__":
    main()
