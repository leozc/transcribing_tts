"""PyInstaller entry point for the standalone tts-serve-api binary.

The API server has no ML dependencies (FastAPI + uvicorn + SQLite + Pydantic), so it
packages into a small self-contained executable. The GPU worker is intentionally NOT
binarized — it needs torch/CUDA and downloads the ~17GB model at runtime — so run it
from the installed environment (`tts-serve-worker`) alongside this binary.
"""
from tts_serve.service.api import main

if __name__ == "__main__":
    main()
