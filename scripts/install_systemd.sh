#!/usr/bin/env bash
# Install tts_serve as systemd USER services: the API (standalone binary) + the GPU
# worker (venv). User services + linger => they survive logout/reboot. Logs go to
# journald (and to <DATA>/logs/*.log). This script only INSTALLS + reloads; start with:
#     systemctl --user enable --now tts-api tts-worker
# Tail logs:
#     journalctl --user -u tts-worker -f
#     journalctl --user -u tts-api -f
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PORT="${TTS_SERVE_PORT:-8088}"
DATA="${TTS_SERVE_DATA:-$REPO/data}"
BIN="$REPO/dist/tts-serve-api"

[ -x "$BIN" ] || { echo "build the API binary first:  bash scripts/build_api_binary.sh"; exit 1; }
[ -x "$REPO/.venv/bin/tts-serve-worker" ] || { echo "install the package first:  VIRTUAL_ENV=.venv uv pip install -e '.[service,diarize]'"; exit 1; }
mkdir -p "$UDIR"

cat > "$UDIR/tts-api.service" <<EOF
[Unit]
Description=tts_serve API (standalone binary)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO
Environment=TTS_SERVE_PORT=$PORT
Environment=TTS_SERVE_DATA=$DATA
Environment=TTS_SERVE_LOG_LEVEL=INFO
EnvironmentFile=-$REPO/.env_service
ExecStart=$BIN
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

cat > "$UDIR/tts-worker.service" <<EOF
[Unit]
Description=tts_serve GPU worker (resident VibeVoice-ASR)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
Environment=TTS_SERVE_DATA=$DATA
Environment=TTS_SERVE_LOG_LEVEL=INFO
Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# On-demand GPU: load the model on the first task, free VRAM after this many idle
# seconds (0 = keep resident). 600s = reclaim the GPU ~10min after the last job.
Environment=TTS_SERVE_IDLE_UNLOAD=600
EnvironmentFile=-$REPO/.env_service
ExecStart=$REPO/.venv/bin/tts-serve-worker
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "installed -> $UDIR/{tts-api,tts-worker}.service  (data=$DATA, port=$PORT)"
echo "secrets:   put DEEPSEEK_API_KEY=... / AWS_* / YT_COOKIES=... in $REPO/.env_service (optional)"
echo "start:     systemctl --user enable --now tts-api tts-worker"
echo "logs:      journalctl --user -u tts-worker -f"
