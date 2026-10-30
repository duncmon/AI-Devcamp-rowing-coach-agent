#!/usr/bin/env bash
# ==============================================================================
# run-backend.sh — Rowing Coach Agent FastAPI ASGI Server Launcher
# ==============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_ROOT}"

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

echo "=========================================================="
echo " 🚣‍♂️ [run-backend] Launching Rowing Coach FastAPI ASGI Backend"
echo " [run-backend] Origin: http://${HOST}:${PORT}"
echo " [run-backend] Health: http://${HOST}:${PORT}/healthz"
echo " [run-backend] Stream: http://${HOST}:${PORT}/api/stream_reasoning_engine"
echo "=========================================================="

exec uv run uvicorn main:app --host "${HOST}" --port "${PORT}" --reload
