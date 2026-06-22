#!/usr/bin/env bash
# E2E: 3 clients (youtube | bilibili | file+multiple) -> single-GPU server -> client3 pulls.
# Each client registers once (POST /v1/clients) -> secret client_key, sent as X-Client-Key to
# enqueue and to LIST ITS OWN jobs. Each create also returns a per-task pull_token (X-Task-Token)
# used here to poll/pull a single task.
# Prereq: tts-serve-worker + tts-serve-api running. Usage: bash benchmark/e2e/run.sh [base_url]
set -euo pipefail
B="${1:-http://localhost:8091}"
cd "$(dirname "$0")/../.."
J='-H content-type:application/json'
id() { python3 -c 'import sys,json;print(json.load(sys.stdin)["task_id"])'; }
tk() { python3 -c 'import sys,json;print(json.load(sys.stdin)["pull_token"])'; }
key() { python3 -c 'import sys,json;print(json.load(sys.stdin)["client_key"])'; }

echo "# register clients -> each gets a secret client_key (X-Client-Key)"
K1=$(curl -s $J -d '{"client_id":"client1"}' "$B/v1/clients" | key)
K2=$(curl -s $J -d '{"client_id":"client2"}' "$B/v1/clients" | key)
K3=$(curl -s $J -d '{"client_id":"client3"}' "$B/v1/clients" | key)

echo "# submit (concurrent) — authenticated via X-Client-Key; response carries the pull_token"
R1=$(curl -s $J -H "X-Client-Key: $K1" -d '{"source":"https://youtu.be/3Amlu4y94Ho","client_id":"client1","clip":"0-20","name":"c1_youtube"}'  "$B/v1/tasks"); C1=$(echo "$R1"|id); T1=$(echo "$R1"|tk)
R2=$(curl -s $J -H "X-Client-Key: $K2" -d '{"source":"https://b23.tv/fZQNYqJ","client_id":"client2","clip":"0-60","name":"c2_bilibili"}'         "$B/v1/tasks"); C2=$(echo "$R2"|id); T2=$(echo "$R2"|tk)
R3A=$(curl -s -H "X-Client-Key: $K3" -F file=@benchmark/sample/allin_60s_16k.wav -F client_id=client3 -F speakers=2 -F name=c3_file           "$B/v1/tasks/upload"); C3A=$(echo "$R3A"|id); T3A=$(echo "$R3A"|tk)
R3B=$(curl -s $J -H "X-Client-Key: $K3" -d '{"source":"https://youtu.be/3Amlu4y94Ho","client_id":"client3","clip":"40-55","name":"c3_req2"}'    "$B/v1/tasks"); C3B=$(echo "$R3B"|id); T3B=$(echo "$R3B"|tk)
R3C=$(curl -s $J -H "X-Client-Key: $K3" -d '{"source":"https://youtu.be/3Amlu4y94Ho","client_id":"client3","clip":"60-75","name":"c3_req3"}'    "$B/v1/tasks"); C3C=$(echo "$R3C"|id); T3C=$(echo "$R3C"|tk)
echo "  c1=$C1 c2=$C2 c3a=$C3A c3b=$C3B c3c=$C3C"

echo "# client3 lists ITS OWN jobs (X-Client-Key) — should see exactly its 3 tasks"
curl -s "$B/v1/tasks" -H "X-Client-Key: $K3" | python3 -c 'import sys,json;d=json.load(sys.stdin)["tasks"];print("  client3 sees",len(d),"jobs:",sorted(t["client_id"] for t in d))'

echo "# poll each task with its pull_token until terminal (one running at a time)"
PAIRS="$C1:$T1 $C2:$T2 $C3A:$T3A $C3B:$T3B $C3C:$T3C"
while :; do
  curl -s "$B/v1/queue" | python3 -c 'import sys,json;d=json.load(sys.stdin);r=d["running"];print("  running",(r["task_id"][:8] if r else "-"),"queued",len(d["queued"]),d["counts"])' || true
  term=0
  for pair in $PAIRS; do
    s=$(curl -s "$B/v1/tasks/${pair%%:*}" -H "X-Task-Token: ${pair##*:}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
    [[ "$s" == done || "$s" == failed ]] && term=$((term+1))
  done
  [ "$term" = 5 ] && break; sleep 10
done

echo "# client3 pulls its 3 tasks (each with its pull_token)"
mkdir -p benchmark/e2e/client3_artifacts
for pair in "$C3A:$T3A" "$C3B:$T3B" "$C3C:$T3C"; do
  curl -s -H "X-Task-Token: ${pair##*:}" -o "benchmark/e2e/client3_artifacts/${pair%%:*}.zip" "$B/v1/tasks/${pair%%:*}/artifact"
  echo "  ${pair%%:*} -> $(unzip -Z1 benchmark/e2e/client3_artifacts/${pair%%:*}.zip 2>/dev/null | tr '\n' ' ')"
done
