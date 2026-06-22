#!/usr/bin/env bash
# Build a single-file, self-contained binary for the API server -> dist/tts-serve-api
# It needs NO Python/venv at runtime. The GPU worker is NOT binarized (torch/CUDA + the
# ~17GB model); run it from the installed env: `tts-serve-worker`.
#
# Prereq (uv-managed venv):  VIRTUAL_ENV=.venv uv pip install pyinstaller
# Usage:                     bash scripts/build_api_binary.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PYI=".venv/bin/pyinstaller"

# Follow the API's real import graph (light: fastapi/uvicorn/pydantic/sqlite + sources.classify).
# Do NOT --collect-submodules tts_serve: that force-bundles asr.py/diarize.py -> torch (~3GB).
# The API never imports the ML / download stack at runtime, so exclude it explicitly.
"$PYI" --onefile --name tts-serve-api --clean --noconfirm \
  --paths src \
  --hidden-import tts_serve.sources \
  --collect-all uvicorn \
  --exclude-module torch --exclude-module transformers --exclude-module speechbrain \
  --exclude-module torchaudio --exclude-module scipy --exclude-module sklearn \
  --exclude-module scikit_learn --exclude-module pandas --exclude-module matplotlib \
  --exclude-module yt_dlp --exclude-module curl_cffi --exclude-module boto3 \
  --exclude-module gdown --exclude-module googleapiclient --exclude-module mutagen \
  --exclude-module Cryptodome --exclude-module numpy \
  --specpath build \
  --distpath dist \
  --workpath build \
  scripts/tts_serve_api_entry.py

echo
echo "built: dist/tts-serve-api  ($(du -h dist/tts-serve-api | cut -f1))"
echo "run:   TTS_SERVE_PORT=39999 ./dist/tts-serve-api   (no venv needed; 39999 is the default)"
