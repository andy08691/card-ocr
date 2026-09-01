#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# entrypoint.sh — 在一個容器內同時跑 Ollama(LLM) 與 FastAPI(app)
#
# 流程：
#   1. 背景啟動 ollama serve（預設 127.0.0.1:11434）
#   2. 等 ollama 就緒
#   3. 拉 LLM 模型（若已在快取 volume 內則秒過）
#   4. 預抓 MinerU VLM 模型到 HF 快取（第一次約 2.5GB；之後略過）
#   5. 前景啟動 uvicorn（綁 $PORT，Cloud Run 需要）
#
# 這支腳本同時給 Dockerfile 的 ENTRYPOINT 與 Beam Pod 使用。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:4b}"
PORT="${PORT:-8000}"

echo "[entrypoint] starting ollama serve ..."
ollama serve &

echo "[entrypoint] waiting for ollama ..."
until curl -sf "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; do
  sleep 1
done

echo "[entrypoint] pulling LLM model: ${OLLAMA_MODEL} (cached after first boot)"
ollama pull "${OLLAMA_MODEL}"

# 預抓 MinerU VLM 模型到 HF 快取（掛 volume 時只有第一次會下載）。失敗不致命，
# app 啟動時的 warmup 也會觸發下載。
echo "[entrypoint] pre-downloading MinerU VLM models (first boot only) ..."
mineru-models-download -s huggingface -m vlm || echo "[entrypoint] mineru model pre-download skipped/failed (non-fatal)"

echo "[entrypoint] starting uvicorn on :${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
