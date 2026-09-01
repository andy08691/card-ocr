#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy/lightning_setup.sh — 在 Lightning AI GPU Studio 裡「一次性」安裝
#
# 在 Studio 的終端機、專案根目錄執行：  bash deploy/lightning_setup.sh
# Studio 已內建 Python + CUDA 驅動；本腳本負責裝 Ollama、建 venv、裝 MinerU 與
# app 依賴、並把模型抓進持久快取（之後重啟 Studio 免重抓）。
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

echo "[1/5] 安裝 Ollama（本地 LLM server）..."
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

echo "[2/5] 安裝 MinerU(core+vllm) + app 依賴（Lightning Studio 只允許預設 conda 環境，直接裝進去）..."
# Lightning Studio 不允許自建 venv；直接用預設 conda 環境。
# Linux 上 pip 的預設 torch wheel 就是 CUDA 版，直接裝即可用 GPU。
# vllm 引擎：GPU 上做 VLM 解碼比 transformers 快很多（transformers 在 T4 上 ~32s/張）。
# ⚠️ Turing 架構的 T4 對 vLLM 支援是邊緣的；若之後 MinerU 在 vLLM 下報錯/裝不起來，
#    退路：`pip uninstall -y vllm`（MINERU_BACKEND=vlm-engine 會自動退回 transformers，慢但能動）。
pip install -U pip
pip install -U "mineru[core,vllm]"
pip install -U -r requirements.txt
# Lightning 預設 conda 常帶「舊 scipy + 被 mineru 升上來的 numpy 2.x」的衝突
# （舊 scipy 會 `from numpy import Inf`，numpy 2.0 起已移除 → transformers import 失敗）。
# 升 scipy / scikit-learn 對齊 numpy 2.x（Lightning base 預裝的舊版會卡 numpy<2）：
pip install -U scipy scikit-learn

echo "[3/5] 啟動 ollama serve（背景）..."
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >/tmp/ollama.log 2>&1 &
fi
until curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; do sleep 1; done

echo "[4/5] 下載 LLM 模型 qwen3:4b ..."
ollama pull qwen3:4b

echo "[5/5] 預抓 MinerU VLM 模型（進 ~/.cache/huggingface，持久快取）..."
mineru-models-download -s huggingface -m vlm || echo "(MinerU 模型預抓略過，app 首次會自動下載)"

echo ""
echo "✅ 安裝完成。啟動服務：  bash deploy/lightning_run.sh"
echo "   （若重啟 Studio 後模型又重抓，見 deploy/lightning.md 的持久化備註）"
