"""Centralized logging for the service processes (api + worker).

Logs go to **stdout** (so `journalctl --user -u tts-api/-worker` captures them under
systemd) AND to a **rotating file** under ``<DATA>/logs/<component>.log`` (so the
standalone binary still leaves a debuggable trail). Configure with env:

    TTS_SERVE_LOG_LEVEL   DEBUG|INFO|WARNING|ERROR   (default INFO)
    TTS_SERVE_LOG_DIR     directory for the file log, or "none" to disable
                          (default: <DATA>/logs)
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(component: str) -> logging.Logger:
    """Configure root logging once for a process; return the component logger.
    Idempotent (safe to call from both startup hook and main())."""
    global _CONFIGURED
    logger = logging.getLogger(f"tts_serve.{component}")
    if _CONFIGURED:
        return logger

    level = os.environ.get("TTS_SERVE_LOG_LEVEL", "INFO").upper()
    fmt = logging.Formatter(
        f"%(asctime)s %(levelname)-7s [{component}] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    root = logging.getLogger()
    root.setLevel(level)

    sh = logging.StreamHandler(sys.stdout)   # -> journald under systemd
    sh.setFormatter(fmt)
    root.addHandler(sh)

    logdir = os.environ.get("TTS_SERVE_LOG_DIR")
    if logdir != "none":
        from tts_serve.service import store
        d = Path(logdir) if logdir else (store.DATA / "logs")
        try:
            d.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(d / f"{component}.log", maxBytes=10 * 1024 * 1024, backupCount=5)
            fh.setFormatter(fmt)
            root.addHandler(fh)
            logger.info("file log -> %s", d / f"{component}.log")
        except OSError as e:
            logger.warning("file logging disabled (%s)", e)

    # let uvicorn's loggers flow through our handlers/format instead of its own
    for n in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(n)
        lg.handlers = []
        lg.propagate = True

    _CONFIGURED = True
    return logger
