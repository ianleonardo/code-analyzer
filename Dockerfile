# syntax=docker/dockerfile:1
# Code Analyzer in one image: the API and the built frontend on port 8001 (same layout as SpecForge).

# ---- frontend build ----
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
ARG NPM_CONFIG_REGISTRY
WORKDIR /src
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci --no-audit --no-fund
COPY frontend ./frontend
RUN npm run build -w frontend

# ---- runtime ----
FROM python:3.12-slim-bookworm
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

# git clones the analysed repositories; curl is for the health check.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /src/frontend/dist ./frontend

ENV DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend

RUN chmod -R a+rX /app && mkdir -p /data && chmod 0777 /data

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/api/health || exit 1

# One worker process: DuckDB allows a single writing process; analyses run on background threads (ANALYSIS_WORKERS).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips", "*"]
