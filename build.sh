#!/usr/bin/env bash
# Build Code Analyzer Docker image targeting Linux x86 (linux/amd64).
# Usage: ./build.sh [IMAGE_TAG]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

IMAGE_NAME="${1:-${IMAGE_NAME:-code-analyzer:latest}}"
PLATFORM="${PLATFORM:-linux/amd64}"

echo "=========================================================="
echo " Building Code Analyzer Docker Image"
echo " Target Platform : ${PLATFORM}"
echo " Image Tag       : ${IMAGE_NAME}"
echo " Context Dir     : ${ROOT}"
echo "=========================================================="

# Check if Docker is installed
if ! command -v docker >/dev/null 2>&1; then
  echo "Error: 'docker' command not found. Please install Docker." >&2
  exit 1
fi

# Build for linux/amd64
docker build \
  --platform "${PLATFORM}" \
  -t "${IMAGE_NAME}" \
  -f "${ROOT}/Dockerfile" \
  "${ROOT}"

echo ""
echo "=========================================================="
echo " Build successful: ${IMAGE_NAME} (${PLATFORM})"
echo " To start the container, run: ./start.sh"
echo "=========================================================="
