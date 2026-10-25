#!/usr/bin/env bash
# Start Code Analyzer container (Linux x86) with external data and .env file.
# Usage: ./start.sh [PORT]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

IMAGE_NAME="${IMAGE_NAME:-code-analyzer:latest}"
CONTAINER_NAME="${CONTAINER_NAME:-code-analyzer}"
PLATFORM="${PLATFORM:-linux/amd64}"
HOST_DATA_DIR="${DATA_DIR:-${ROOT}/data}"
ENV_FILE="${ENV_FILE:-${ROOT}/.env}"
PORT="${1:-${PORT:-8001}}"

# Ensure host data directory exists outside the container
mkdir -p "$HOST_DATA_DIR"

# Check if Docker image is available; if not, check for archive to load or Dockerfile to build
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  ARCHIVE=""
  for candidate in \
    "${ROOT}/code-analyzer-x86_64.tar.gz" \
    "${ROOT}/code-analyzer.tar.gz" \
    "${ROOT}/code-analyzer-latest.tar.gz" \
    "${ROOT}/code-analyzer.tar" \
    $(ls "${ROOT}"/code-analyzer*.tar.gz 2>/dev/null || true) \
    $(ls "${ROOT}"/*.tar.gz 2>/dev/null || true); do
    if [ -f "$candidate" ]; then
      ARCHIVE="$candidate"
      break
    fi
  done

  if [ -n "$ARCHIVE" ]; then
    echo "=========================================================="
    echo " Image '${IMAGE_NAME}' not found in Docker engine."
    echo " Detected image archive: ${ARCHIVE}"
    echo " Automatically loading image into Docker..."
    echo "=========================================================="
    docker load < "$ARCHIVE"
    echo "Image '${IMAGE_NAME}' loaded successfully!"
    echo ""
  elif [ -f "${ROOT}/Dockerfile" ]; then
    echo "Image '${IMAGE_NAME}' not found in Docker. Building image locally..."
    "${ROOT}/build.sh" "$IMAGE_NAME"
  else
    echo "Error: Docker image '${IMAGE_NAME}' not found, and no image archive (.tar.gz) was found in ${ROOT}." >&2
    echo "Please copy 'code-analyzer-x86_64.tar.gz' to this directory or build the image using ./build.sh." >&2
    exit 1
  fi
fi

# If host data directory is empty but backend/data has an existing DuckDB database, copy it over
if [ ! -f "$HOST_DATA_DIR/codeanalyzer.duckdb" ] && [ -f "$ROOT/backend/data/codeanalyzer.duckdb" ]; then
  echo "Notice: Detected existing database at backend/data/. Copying to ${HOST_DATA_DIR}..."
  cp "$ROOT/backend/data/codeanalyzer.duckdb"* "$HOST_DATA_DIR/" 2>/dev/null || true
  if [ -d "$ROOT/backend/data/uploads" ] && [ ! -d "$HOST_DATA_DIR/uploads" ]; then
    cp -r "$ROOT/backend/data/uploads" "$HOST_DATA_DIR/" 2>/dev/null || true
  fi
fi

# Prepare external .env arguments
ENV_ARGS=()
if [ -f "$ENV_FILE" ]; then
  echo "Reading environment file from: ${ENV_FILE}"
  ENV_ARGS+=(--env-file "$ENV_FILE")
  # Read PORT from .env if defined and no port was passed explicitly
  if [ "$PORT" = "8001" ]; then
    ENV_PORT=$(grep -E '^(FRONTEND_PORT|PORT)=' "$ENV_FILE" | cut -d '=' -f2- | tr -d ' "\r' | head -n1 || true)
    if [ -n "$ENV_PORT" ]; then
      PORT="$ENV_PORT"
    fi
  fi
else
  echo "Notice: No .env file found at ${ENV_FILE}. Running with default configuration."
  if [ -f "$ROOT/.env.example" ]; then
    echo "Tip: You can copy .env.example to .env to customize settings, keys, and ports."
  fi
fi

# Check if container is already running
if docker ps --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
  echo "Container '${CONTAINER_NAME}' is already running."
  echo "Access the app at: http://localhost:${PORT}"
  echo "To view logs: docker logs -f ${CONTAINER_NAME}"
  exit 0
fi

# Remove any stopped container with the same name
if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
  echo "Removing existing stopped container '${CONTAINER_NAME}'..."
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi

echo "=========================================================="
echo " Starting Code Analyzer"
echo " Container Name : ${CONTAINER_NAME}"
echo " Image          : ${IMAGE_NAME} (${PLATFORM})"
echo " Web UI / API   : http://localhost:${PORT}"
echo " Host Data Dir  : ${HOST_DATA_DIR} (mounted to /data)"
echo " Host Env File  : ${ENV_FILE}"
echo "=========================================================="

docker run -d \
  --name "$CONTAINER_NAME" \
  --platform "$PLATFORM" \
  -p "${PORT}:8001" \
  -v "${HOST_DATA_DIR}:/data" \
  "${ENV_ARGS[@]}" \
  -e DATA_DIR=/data \
  --restart unless-stopped \
  "$IMAGE_NAME"

echo ""
echo "Code Analyzer container '${CONTAINER_NAME}' is up and running!"
echo "URL          : http://localhost:${PORT}"
echo "Health check : http://localhost:${PORT}/api/health"
echo "View logs    : docker logs -f ${CONTAINER_NAME}"
echo "Stop app     : ./stop.sh"
