#!/bin/bash
# start.cloud.sh — 啟動雲端管線（cloud.main:app）
#
# 對照 start.sh（本機 GPU 管線，app.main:app，port 8000）。
# 兩者可以同時跑：埠不同、env 檔不同、圖片目錄不同。
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 優先用雲端專用 venv，其次沿用 MinerU 的（openai 已在其中）
if [ -f "$SCRIPT_DIR/.venv_cloud/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv_cloud/bin/activate"
elif [ -f "$SCRIPT_DIR/.venv_mineru/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv_mineru/bin/activate"
else
    echo "ERROR: No virtual environment found. See deploy/cloud/README.md."
    exit 1
fi

# 注意：這裡不 source .env.cloud——cloud/config.py 會自己 load_dotenv()，
# 且必須由它來做，才能保證在 app.database 讀 DATABASE_URL 之前完成。
# 這裡只讀出 host/port 用來組 uvicorn 參數。
CLOUD_HOST=${CLOUD_HOST:-$(grep -E '^CLOUD_HOST=' "$SCRIPT_DIR/.env.cloud" 2>/dev/null | cut -d= -f2)}
CLOUD_PORT=${CLOUD_PORT:-$(grep -E '^CLOUD_PORT=' "$SCRIPT_DIR/.env.cloud" 2>/dev/null | cut -d= -f2)}
CLOUD_HOST=${CLOUD_HOST:-0.0.0.0}
CLOUD_PORT=${CLOUD_PORT:-8100}

echo "Starting cloud OCR server on http://$CLOUD_HOST:$CLOUD_PORT"
exec uvicorn cloud.main:app --host "$CLOUD_HOST" --port "$CLOUD_PORT" --workers 1
