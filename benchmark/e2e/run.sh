#!/usr/bin/env bash
# E2E: 3 clients (youtube | bilibili | file+multiple) -> single-GPU server -> client3 pulls.
# Prereq: tts-serve-worker + tts-serve-api running. Usage: bash benchmark/e2e/run.sh [base_url]
set -euo pipefail
B="${1:-http://localhost:8091}"
cd "$(dirname "$0")/../.."
J='-H content-type:application/json'

echo "# submit (concurrent) — each client passes its own client_id"
C1=$(curl -s $J -d '{"source":"https://youtu.be/3Amlu4y94Ho","client_id":"client1","clip":"0-20","name":"c1_youtube"}'  "$B/v1/tasks"        | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
C2=$(curl -s $J -d '{"source":"https://b23.tv/fZQNYqJ","client_id":"client2","name":"c2_bilibili"}'                      "$B/v1/tasks"        | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
C3A=$(curl -s -F file=@benchmark/sample/allin_60s_16k.wav -F client_id=client3 -F speakers=2 -F name=c3_file            "$B/v1/tasks/upload" | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
C3B=$(curl -s $J -d '{"source":"https://youtu.be/3Amlu4y94Ho","client_id":"client3","clip":"40-55","name":"c3_req2"}'    "$B/v1/tasks"        | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
C3C=$(curl -s $J -d '{"source":"https://youtu.be/3Amlu4y94Ho","client_id":"client3","clip":"60-75","name":"c3_req3"}'    "$B/v1/tasks"        | python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])')
echo "  c1=$C1 c2=$C2 c3a=$C3A c3b=$C3B c3c=$C3C"

echo "# poll queue until all terminal (one running at a time; /v1/queue is admin view)"
PAIRS="$C1:client1 $C2:client2 $C3A:client3 $C3B:client3 $C3C:client3"
while :; do
  curl -s "$B/v1/queue" | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d["running"];print("  running",(r["task_id"][:8] if r else "-"),"queued",len(d["queued"]),d["counts"])'
  term=0
  for pair in $PAIRS; do
    s=$(curl -s "$B/v1/tasks/${pair%%:*}" -H "X-Client-Id: ${pair##*:}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
    [[ "$s" == done || "$s" == failed ]] && term=$((term+1))
  done
  [ "$term" = 5 ] && break; sleep 10
done

echo "# client3 pulls its 3 tasks (with its client_id)"
mkdir -p benchmark/e2e/client3_artifacts
for t in $C3A $C3B $C3C; do
  curl -s -H "X-Client-Id: client3" -o "benchmark/e2e/client3_artifacts/$t.zip" "$B/v1/tasks/$t/artifact"
  echo "  $t -> $(unzip -Z1 benchmark/e2e/client3_artifacts/$t.zip | tr '\n' ' ')"
done
