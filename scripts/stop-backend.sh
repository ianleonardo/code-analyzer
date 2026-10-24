#!/usr/bin/env bash
# Stop the backend listening on port 8001. Run from anywhere: ./scripts/stop-backend.sh

set -euo pipefail

PORT=8001
NAME="Backend"

listener_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' || true
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr ' ' '\n' || true
  fi
}

pids="$(listener_pids | awk 'NF && !seen[$1]++')"
if [[ -z "$pids" ]]; then
  echo "$NAME is not running."
  exit 0
fi

# Also stop a uvicorn or npm parent, so a reload worker is not started again.
extra=""
for pid in $pids; do
  parent="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  if [[ -z "$parent" || "$parent" == "1" ]]; then
    continue
  fi
  cmd="$(ps -o command= -p "$parent" 2>/dev/null || true)"
  case "$cmd" in
    *uvicorn*|*npm*|*node*) extra="$extra $parent" ;;
  esac
done

echo "Stopping $NAME on port $PORT..."
# shellcheck disable=SC2086
kill $pids $extra 2>/dev/null || true
sleep 0.4
still=""
for pid in $pids $extra; do
  if kill -0 "$pid" 2>/dev/null; then
    still="$still $pid"
  fi
done
if [[ -n "$still" ]]; then
  # shellcheck disable=SC2086
  kill -9 $still 2>/dev/null || true
fi
echo "$NAME stopped."
