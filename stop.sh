#!/bin/bash
pkill -f "uvicorn app.main" 2>/dev/null
pkill -f "multiprocessing-fork" 2>/dev/null
sleep 0.5

# Kill all processes on port 8000 and wait until the port is actually free
for i in {1..20}; do
    PIDS=$(lsof -ti :8000 2>/dev/null)
    [ -z "$PIDS" ] && break
    echo "$PIDS" | xargs kill -9 2>/dev/null
    sleep 0.5
done

# Final check — exit with error if port is still in use
if lsof -ti :8000 &>/dev/null; then
    echo "ERROR: Port 8000 is still in use. Try rebooting or run: sudo kill -9 \$(lsof -ti :8000)"
    exit 1
fi

echo "Port 8000 is free. Starting server..."
