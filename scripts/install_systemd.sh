#!/usr/bin/env bash
# Install tts_serve as systemd USER services: the API (standalone binary) + the GPU
# worker (venv). User services + linger => they survive logout/reboot. State lives in
# the systemd StateDirectory (~/.local/state/tts_serve), NOT in the repo. Logs go to
# journald (and <DATA>/logs/*.log). This script only INSTALLS + reloads; start with:
#     systemctl --user enable --now tts-api tts-worker
# Tail logs:  journalctl --user -u tts-worker -f   /   journalctl --user -u tts-api -f
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PORT="${TTS_SERVE_PORT:-39999}"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/tts_serve"   # = systemd %S/tts_serve
BIN="$REPO/dist/tts-serve-api"

[ -x "$BIN" ] || { echo "build the API binary first:  bash scripts/build_api_binary.sh"; exit 1; }
[ -x "$REPO/.venv/bin/tts-serve-worker" ] || { echo "install the package first:  VIRTUAL_ENV=.venv uv pip install -e '.[service,diarize]'"; exit 1; }
mkdir -p "$UDIR"

# Both units: StateDirectory=tts_serve makes systemd create+own ~/.local/state/tts_serve
# and exposes it as %S/tts_serve, which we hand to the app via TTS_SERVE_DATA.
cat > "$UDIR/tts-api.service" <<EOF
[Unit]
Description=tts_serve API (standalone binary)
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO
StateDirectory=tts_serve
Environment=TTS_SERVE_PORT=$PORT
Environment=TTS_SERVE_DATA=%S/tts_serve
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
Description=tts_serve GPU worker (VibeVoice-ASR, on-demand)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
StateDirectory=tts_serve
Environment=TTS_SERVE_DATA=%S/tts_serve
Environment=TTS_SERVE_LOG_LEVEL=INFO
Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# On-demand GPU: model loads on the first task. RECYCLE=1 -> a child process runs the
# model and EXITS after IDLE_UNLOAD idle seconds, freeing the GPU 100% between bursts
# (set RECYCLE=0 to keep one process and free only the weights; IDLE_UNLOAD=0 = resident).
Environment=TTS_SERVE_GPU_RECYCLE=1
Environment=TTS_SERVE_IDLE_UNLOAD=600
EnvironmentFile=-$REPO/.env_service
ExecStart=$REPO/.venv/bin/tts-serve-worker
Restart=on-failure
RestartSec=5
# Send SIGTERM only to the supervisor (main PID); it drains/escalates to its child.
# After 45s systemd SIGKILLs anything left, so a stuck transcribe can't block shutdown.
KillMode=mixed
TimeoutStopSec=45

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "installed -> $UDIR/{tts-api,tts-worker}.service  (state=$STATE, port=$PORT)"
if [ -f "$REPO/data/tasks.db" ]; then
  echo "MIGRATE existing state out of the repo, then RENAME the source so it can't"
  echo "be reopened as a stale duplicate DB (split-brain):"
  echo "    mkdir -p '$STATE' && cp -a '$REPO/data/.' '$STATE/' && mv '$REPO/data' '$REPO/data.migrated'"
fi
echo "secrets:   put DEEPSEEK_API_KEY=... / AWS_* / YT_COOKIES=... in $REPO/.env_service (optional)"
echo "start:     systemctl --user enable --now tts-api tts-worker"
echo "logs:      journalctl --user -u tts-worker -f"
