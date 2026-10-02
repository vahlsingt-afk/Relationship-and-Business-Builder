#!/bin/bash
# RB API Server — startup script
# Usage: ./system/api/start_server.sh [--port PORT] [--host HOST]
#
# Requires: pip install fastapi uvicorn --break-system-packages
# Set RB_API_KEY environment variable before running in production.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
PORT="${RB_PORT:-8765}"
HOST="${RB_HOST:-127.0.0.1}"

if [[ -z "${RB_API_KEY:-}" ]]; then
  echo "WARNING: RB_API_KEY is not set. The API will run without authentication."
  echo "         Set RB_API_KEY before exposing this service on a network."
fi

cd "$PROJECT_DIR"
echo "Starting Relationship Builder API on $HOST:$PORT"
echo "Project dir: $PROJECT_DIR"
echo "Press Ctrl+C to stop."
echo ""

exec uvicorn system.api.server:app \
  --host "$HOST" \
  --port "$PORT" \
  --reload \
  --log-level info
