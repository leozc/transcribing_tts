"""GPU worker: drains the FIFO task queue, one task at a time (GPU concurrency = 1).

Three model-allocation modes (env), trading idle VRAM for first-task latency:

* **resident**   (`TTS_SERVE_IDLE_UNLOAD=0`, default): load once at startup, stay hot.
* **idle-unload** (`TTS_SERVE_IDLE_UNLOAD=N`): single long-lived process; after N idle
  seconds drop the model + ``empty_cache()`` — frees the ~17GB of weights but a small
  CUDA context (~hundreds of MB) lingers until the process exits.
* **recycle**    (`TTS_SERVE_GPU_RECYCLE=1` with `IDLE_UNLOAD=N`): a tiny supervisor
  (never touches CUDA -> 0 GPU) spawns a child that loads the model on demand, drains
  the queue, and **exits** after N idle seconds — so the GPU is **100%** freed between
  bursts. The child reloads on the next task (cold start ~ model load time).

Since this is async batch work (~75 min GPU/day), the cold-start cost is negligible and
recycle is the right default for a shared GPU.
"""
from __future__ import annotations

import os

# Reduce cross-task GPU fragmentation (must precede torch import).
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import logging
import multiprocessing as mp
import signal
import time

from tts_serve import core
from tts_serve.outputs import write_outputs
from tts_serve.service import store
from tts_serve.service.logconf import setup_logging
from tts_serve.sources import SourceOpts

log = logging.getLogger("tts_serve.worker")  # configured by setup_logging() in each entrypoint

_STOP = False


def _on_term(signum, _frame) -> None:
    global _STOP
    _STOP = True
    log.info("received signal %s; finishing current poll then stopping", signum)


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


def _outputs_complete(tid: str) -> bool:
    """True if a previous run already wrote the full artifact set. Lets a crash in the
    tiny gap between write_outputs() and the status='done' update recover without an
    expensive GPU re-transcribe: the reclaimed task is just re-marked done."""
    rdir = store.results_dir(tid)
    return all((rdir / f).exists() for f in ("segments.json", "meta.json", "transcript.txt"))


def _free_gpu() -> None:
    """Release the model's GPU memory back to the driver. Frees the ~17GB of weights;
    a small CUDA context (~hundreds of MB) remains until the process exits."""
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:  # noqa: BLE001 — torch may be absent on a no-GPU dev box
        pass


def _should_spawn(counts: dict, child_alive: bool) -> bool:
    """Recycle supervisor: spawn a child only when there's pending work and none is
    running. 'running' is included so a task orphaned by a crashed child (left running)
    triggers a respawn whose reclaim_stale() requeues it."""
    if child_alive:
        return False
    return (counts.get("queued", 0) + counts.get("running", 0)) > 0


def _worker_loop(idle_unload: float, *, exit_on_idle: bool) -> None:
    """Claim + process tasks until stopped. After ``idle_unload`` idle seconds: return
    (exit_on_idle, used by the recycle child -> process exits -> GPU 100% free) or free
    the model in place (long-lived process). ``idle_unload<=0`` keeps the model resident."""
    store.init()
    reclaimed = store.reclaim_stale()  # requeue tasks orphaned by a previous crash
    if reclaimed:
        log.warning("re-queued %d stale running task(s) from a previous crash", reclaimed)
    poll = float(os.environ.get("TTS_SERVE_POLL", "1.0"))
    retention = float(os.environ.get("TTS_SERVE_RETENTION_DAYS", "7"))

    asr = None
    if idle_unload <= 0 and not exit_on_idle:
        asr = _load_model()  # resident
    log.info("polling queue")
    last_maint = 0.0
    last_active = time.monotonic()
    while not _STOP:
        # lifecycle maintenance roughly hourly (purge old terminal tasks + WAL checkpoint)
        if time.time() - last_maint > 3600:
            n = store.purge_old(retention)
            if n:
                log.info("purged %d old task(s) (retention=%.0fd)", n, retention)
            last_maint = time.time()

        task = store.claim_next_queued()
        if not task:
            idle_for = time.monotonic() - last_active
            if idle_unload > 0 and idle_for > idle_unload:
                if exit_on_idle:
                    log.info("idle %.0fs > %.0fs: exiting child to release the GPU completely", idle_for, idle_unload)
                    return
                if asr is not None:
                    log.info("idle %.0fs > %.0fs: unloading model to free GPU weights", idle_for, idle_unload)
                    asr = None
                    _free_gpu()
                    log.info("model unloaded; weights freed (CUDA context persists)")
            time.sleep(poll)
            continue

        log.info("claimed %s (client=%s type=%s)", task["id"], task.get("client_id"), task["source_type"])
        t0 = time.monotonic()
        try:
            if _outputs_complete(task["id"]):  # crashed after writing outputs, before 'done'
                store.update(task["id"], status="done", stage="done")
                log.info("task %s already has complete outputs; marked done (no re-transcribe)", task["id"])
            else:
                if asr is None:  # on-demand cold start
                    store.update(task["id"], stage="loading_model")
                    log.info("loading model on demand for task %s ...", task["id"])
                    asr = _load_model()
                _process(asr, task)
                log.info("task %s finished in %.1fs", task["id"], time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001
            store.update(task["id"], status="failed", error=str(e))
            log.error("task %s FAILED after %.1fs: %s", task["id"], time.monotonic() - t0, e, exc_info=True)
        last_active = time.monotonic()


def _child_main() -> None:
    """Entry for a spawned recycle child (fresh process): on-demand load, exit on idle."""
    setup_logging("worker")  # fresh spawned process: configure its own logging
    # Drain at the next poll boundary on stop instead of dying mid-poll. (A long
    # _process() call still can't be interrupted mid-transcribe; KillMode=mixed +
    # the supervisor's terminate->kill escalation bound the shutdown time.)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    idle = float(os.environ.get("TTS_SERVE_IDLE_UNLOAD", "0")) or 60.0
    log.info("worker child pid=%d started; loads model on demand, exits after %.0fs idle", os.getpid(), idle)
    _worker_loop(idle, exit_on_idle=True)


def _supervisor(idle_unload: float) -> None:
    """Recycle mode: stay lightweight (no CUDA -> 0 GPU), spawn one child while there is
    work, reap it when it idle-exits. Each child fully frees the GPU on exit."""
    store.init()
    poll = float(os.environ.get("TTS_SERVE_POLL", "1.0"))
    log.info("worker starting | data=%s db=%s | model=on-demand RECYCLE "
             "(child loads on demand, exits after %.0fs idle -> GPU 100%% free)",
             store.DATA, store.DB, idle_unload)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    ctx = mp.get_context("spawn")
    # children log to stdout/journald only (one file-handler owner avoids rotation races)
    os.environ["TTS_SERVE_LOG_DIR"] = "none"
    child: mp.process.BaseProcess | None = None
    while not _STOP:
        if child is not None and not child.is_alive():
            child.join()
            log.info("worker child pid=%s exited (code=%s); GPU released", child.pid, child.exitcode)
            child = None
        if _should_spawn(store.counts(), child_alive=child is not None):
            child = ctx.Process(target=_child_main, name="tts-worker-child")
            child.start()
            log.info("spawned worker child pid=%d (pending work)", child.pid)
        time.sleep(poll)
    if child is not None and child.is_alive():
        log.info("stopping: terminating worker child pid=%d to free the GPU", child.pid)
        child.terminate()           # SIGTERM -> child drains at its next poll boundary
        child.join(10)
        if child.is_alive():        # mid-transcribe (uninterruptible): escalate to SIGKILL
            log.warning("child pid=%d still alive after 10s; sending SIGKILL", child.pid)
            child.kill()
            child.join()
        log.info("child pid=%s stopped (code=%s)", child.pid, child.exitcode)
    log.info("supervisor exited")


def main() -> None:
    setup_logging("worker")  # configure logging for this process (parent / non-recycle)
    idle_unload = float(os.environ.get("TTS_SERVE_IDLE_UNLOAD", "0"))  # 0 = resident
    recycle = os.environ.get("TTS_SERVE_GPU_RECYCLE", "").strip().lower() in ("1", "true", "yes", "on")
    if recycle:
        _supervisor(idle_unload if idle_unload > 0 else 60.0)
        return
    mode = (f"on-demand in-process (free weights after {idle_unload:.0f}s idle)"
            if idle_unload > 0 else "resident")
    log.info("worker starting | data=%s db=%s | model=%s", store.DATA, store.DB, mode)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    _worker_loop(idle_unload, exit_on_idle=False)


if __name__ == "__main__":
    main()
