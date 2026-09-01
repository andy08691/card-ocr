# ─────────────────────────────────────────────────────────────────────────────
# deploy/Dockerfile — GPU 部署映像（MinerU + Ollama + FastAPI 一個容器）
#
# 可攜：同一份映像可用在 RunPod Pods / Google Cloud Run(GPU) / Fly.io / 任何
# 有 NVIDIA GPU 的容器平台。Beam 亦可（見 deploy/beam_app.py）。
#
# GPU 上用 MinerU 的 vLLM 後端（MINERU_BACKEND=vlm-engine，CUDA 上自動選 vLLM）：
#   - 為了速度：transformers 後端在 GPU 上做 VLM 自迴歸解碼很慢（實測 T4 上 ~32s/張），
#     vLLM 有優化 kernel，1.2B 模型可壓到個位數秒。
#   - 為了與 Ollama 共存 16GB：用 MINERU_VIRTUAL_VRAM_SIZE 限制 vLLM 只吃 ~8GB，
#     其餘留給 Ollama(qwen3:4b ~3–4GB)，避免 vLLM 預佔 90% VRAM 餓死 Ollama。
#   退路：若某張卡 vLLM 裝不起來/不支援（如 Turing T4 邊緣情況），改裝 mineru[core]
#     （不含 vllm），MINERU_BACKEND=vlm-engine 會自動退回 transformers（慢但能動）。
#     見 deploy/README.md。
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

# MinerU core + 釘住相容的 vLLM（0.10.2；vLLM 0.28+ 會讓 MinerU 的 no-repeat-ngram
# logits processor 失效 → 輸出重複亂碼）。用 uv 安裝：pip 對 vLLM 依賴會 backtracking 卡死。
RUN pip install -U pip uv && \
    uv pip install --system "mineru[core]" "vllm==0.10.2"

# 應用層依賴（fastapi/uvicorn/sqlalchemy/opencc/pillow/ollama…）
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

# 應用程式碼與啟動腳本
COPY app ./app
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 執行期預設（可在平台上覆寫）
ENV CARD_EXTRACTOR=llm \
    MINERU_BACKEND=vlm-engine \
    MINERU_VIRTUAL_VRAM_SIZE=8 \
    MINERU_PDF_RENDER_THREADS=1 \
    OLLAMA_MODEL=qwen3:4b \
    OLLAMA_KEEP_ALIVE=-1 \
    PORT=8000
# 注意：不設 OLLAMA_HOST，讓 ollama serve 用預設 127.0.0.1:11434、
# app 用預設 http://localhost:11434（避免 scheme 格式不一致）。

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
