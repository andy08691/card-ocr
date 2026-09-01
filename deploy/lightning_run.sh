#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy/lightning_run.sh — 在 Lightning Studio 啟動服務（ollama + FastAPI）
#
# 執行：  bash deploy/lightning_run.sh
# 之後在 Studio 介面把 port 8000 對外分享，即可用瀏覽器 / curl 測試。
# GPU 上用 MinerU 的 vlm-transformers 後端（1.2B 夠快、免 vLLM、不與 Ollama 搶 VRAM）。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

export CARD_EXTRACTOR="${CARD_EXTRACTOR:-llm}"
export MINERU_BACKEND="${MINERU_BACKEND:-vlm-transformers}"
export MINERU_PDF_RENDER_THREADS="${MINERU_PDF_RENDER_THREADS:-1}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:4b}"

echo "[run] 確認 ollama serve ..."
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  until curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; do sleep 1; done
fi
ollama pull "${OLLAMA_MODEL}" >/dev/null 2>&1 || true

echo "[run] 啟動 FastAPI（GPU: MINERU_BACKEND=${MINERU_BACKEND}）..."
# shellcheck disable=SC1091
source .venv/bin/activate
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
