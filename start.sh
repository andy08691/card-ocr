#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate virtual environment — prefer the MinerU venv, fall back to legacy names
if [ -f "$SCRIPT_DIR/.venv_mineru/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv_mineru/bin/activate"
elif [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
else
    echo "ERROR: No virtual environment found. Please run the install steps first."
    exit 1
fi

# Load .env if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}

echo "Starting OCR server on http://$HOST:$PORT"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1
