#!/usr/bin/env bash
# Stop and remove the Code Analyzer Docker container.
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-code-analyzer}"

echo "Stopping Code Analyzer container '${CONTAINER_NAME}'..."

if docker ps --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
  docker stop "$CONTAINER_NAME"
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "Container '${CONTAINER_NAME}' stopped and removed."
  echo "All database and analysis data remain safely preserved in your host data directory."
elif docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "Removed stopped container '${CONTAINER_NAME}'."
else
  echo "Container '${CONTAINER_NAME}' is not running."
fi
