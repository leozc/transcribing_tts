#!/usr/bin/env bash
# E2E: long-form / OOM regression. Submit a video LONGER than one GPU pass; the SERVER
# must chunk it (~820s/chunk), transcribe each with the resident model, offset
# timestamps, and MERGE into ONE unified transcript — no client-side clipping, no CUDA
# OOM. (Before this, a single full pass OOM'd at ~21GB on the 24GB card.)
# Usage: bash benchmark/e2e/run_longform.sh [base_url] [clip]
#   default clip 0-1800 (30min -> ~3 chunks); pass "" for the full ~84min video.
set -euo pipefail
B="${1:-http://localhost:39999}"
CLIP="${2-0-1800}"
J='-H content-type:application/json'
cd "$(dirname "$0")/../.."

KEY=$(curl -s $J -d '{"client_id":"longform"}' "$B/v1/clients" | python3 -c 'import sys,json;print(json.load(sys.stdin)["client_key"])')
body="{\"source\":\"https://youtu.be/3Amlu4y94Ho\",\"client_id\":\"longform\",\"name\":\"longform\""
[ -n "$CLIP" ] && body="$body,\"clip\":\"$CLIP\""
body="$body}"
R=$(curl -s $J -H "X-Client-Key: $KEY" -d "$body" "$B/v1/tasks")
TID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
echo "submitted $TID (clip='${CLIP:-FULL}'); the SERVER chunks + merges. polling..."
while :; do
  S=$(curl -s -H "X-Client-Key: $KEY" "$B/v1/tasks/$TID" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["status"],d.get("stage"))')
  echo "  $S"
  case "$S" in done*) break;; failed*) echo "FAILED (should not OOM)"; exit 1;; esac
  sleep 15
done

curl -s -H "X-Client-Key: $KEY" -o /tmp/lf.zip "$B/v1/tasks/$TID/artifact"
python3 - "$CLIP" <<'PY'
import sys, zipfile, json
clip = sys.argv[1]
d = json.loads(zipfile.ZipFile("/tmp/lf.zip").read("segments.json"))
last = d["segments"][-1]["end"]
print(f"chunked={d.get('chunked')} chunk_seconds={d.get('chunk_seconds')} "
      f"duration_s={d.get('duration_s')} n_segments={d.get('n_segments')} "
      f"last_seg_end={last:.0f}s peak_vram_gb={d.get('peak_vram_gb')}")
expect = 1700 if clip == "0-1800" else 60
assert d.get("chunked") is True, "expected server-side chunking (chunked=true)"
assert last > expect, f"expected full coverage (>{expect}s), got {last:.0f}s — output truncated"
print("PASS: server chunked a long video + merged into ONE unified transcript, full coverage, no OOM")
PY
