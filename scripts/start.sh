#!/usr/bin/env bash
# Start the backend and the frontend. Ctrl+C stops both.
# Run from anywhere: ./scripts/start.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

backend_pid=""
cleanup() {
  if [[ -n "$backend_pid" ]] && kill -0 "$backend_pid" 2>/dev/null; then
    kill "$backend_pid" 2>/dev/null || true
    wait "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"$ROOT/scripts/start-backend.sh" &
backend_pid=$!
# The backend script exits immediately when its virtual environment is missing.
sleep 0.3
if ! kill -0 "$backend_pid" 2>/dev/null; then
  wait "$backend_pid" || true
  exit 1
fi

"$ROOT/scripts/start-frontend.sh"
