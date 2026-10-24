#!/usr/bin/env bash
# Stop the backend and the frontend. Run from anywhere: ./scripts/stop.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/stop-backend.sh"
"$ROOT/scripts/stop-frontend.sh"
