# ─────────────────────────────────────────────────────────────────────────────
# deploy/Dockerfile — GPU 部署映像（MinerU + Ollama + FastAPI 一個容器）
#
# 可攜：同一份映像可用在 RunPod Pods / Google Cloud Run(GPU) / Fly.io / 任何
# 有 NVIDIA GPU 的容器平台。Beam 亦可（見 deploy/beam_app.py）。
#
# GPU 上刻意使用 MinerU 的 vlm-engine 後端（見 MINERU_BACKEND）：
#   - 1.2B VLM 在 GPU 上 transformers 已夠快（~1–3s）。
#   - 避開 vLLM 的 CUDA 編譯/版本地獄，也避免 vLLM 預佔 VRAM 餓死 Ollama。
#   想榨最後一點速度可改裝 mineru[vllm] 並把 MINERU_BACKEND 設回 vlm-engine，
#   同時加 vLLM 的 --gpu-memory-utilization 0.45（見 deploy/README.md）。
#
# 從專案根目錄建置：  docker build -f deploy/Dockerfile -t card-ocr-gpu .
# ─────────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# 系統套件：python、curl、以及 opencv/pillow 需要的影像庫
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip curl ca-certificates libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/bin/python

# Ollama（本地 LLM server）— install.sh 會放 binary 到 /usr/local/bin
RUN curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app

# MinerU：core 版含 transformers VLM 後端（不含 vllm/mlx），較輕且穩
RUN pip install -U pip && pip install -U "mineru[core]"

# 應用層依賴（fastapi/uvicorn/sqlalchemy/opencc/pillow/ollama…）
COPY requirements.txt .
RUN pip install -r requirements.txt

# 應用程式碼與啟動腳本
COPY app ./app
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 執行期預設（可在平台上覆寫）
ENV CARD_EXTRACTOR=llm \
    MINERU_BACKEND=vlm-engine \
    MINERU_PDF_RENDER_THREADS=1 \
    OLLAMA_MODEL=qwen3:4b \
    OLLAMA_KEEP_ALIVE=-1 \
    PORT=8000
# 注意：不設 OLLAMA_HOST，讓 ollama serve 用預設 127.0.0.1:11434、
# app 用預設 http://localhost:11434（避免 scheme 格式不一致）。

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
