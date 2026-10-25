#!/usr/bin/env bash
# Export the built Docker image to a compressed archive for transfer to a server.
# Usage: ./save.sh [OUTPUT_FILE] [IMAGE_TAG]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

OUTPUT_FILE="${1:-${ROOT}/dist/code-analyzer-x86_64.tar.gz}"
IMAGE_NAME="${2:-${IMAGE_NAME:-code-analyzer:latest}}"

# Check if image exists in local Docker
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "Image '${IMAGE_NAME}' not found locally."
  echo "Building it now..."
  "${ROOT}/build.sh" "$IMAGE_NAME"
fi

echo "=========================================================="
echo " Exporting Docker Image"
echo " Image       : ${IMAGE_NAME}"
echo " Output file : ${OUTPUT_FILE}"
echo "=========================================================="

# Save and gzip compress
docker save "${IMAGE_NAME}" | gzip > "${OUTPUT_FILE}"

FILE_SIZE=$(du -h "${OUTPUT_FILE}" | cut -f1)

echo ""
echo "=========================================================="
echo " Successfully exported image!"
echo " File: ${OUTPUT_FILE} (${FILE_SIZE})"
echo "=========================================================="
echo ""
echo "Next steps to transfer to your server:"
echo "1. Copy file to server:"
echo "   scp ${OUTPUT_FILE} start.sh stop.sh .env user@your-server:/path/to/app/"
echo ""
echo "2. On the server, simply run:"
echo "   ./start.sh"
echo "   (start.sh will automatically detect and load the image archive)"
