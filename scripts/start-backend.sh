#!/usr/bin/env bash
# Start the backend at http://localhost:8001. Run from anywhere: ./scripts/start-backend.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$ROOT/backend/.venv/bin/uvicorn" ]]; then
  echo "Backend virtual environment not found at backend/.venv" >&2
  echo "Create it with:" >&2
  echo "  cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

cd "$ROOT/backend"
echo "Backend: http://localhost:8001"
exec .venv/bin/uvicorn app.main:app --reload --port 8001
