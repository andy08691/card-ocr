# ─────────────────────────────────────────────────────────────────────────────
# ⚠️ DEPRECATED — 請勿使用
#
# 這是 PaddleOCR 時代的遺留檔。PaddleOCR 已在 commit 289a37b 移除，
# requirements.txt 早已沒有它，所以這份檔案「建得起來但跑不動」：
# 模型下載那段靠 `|| true` 無聲跳過、build 會綠燈，然後 CMD 啟動時在
# `import app.services.ocr` 炸掉（MinerU 不在映像裡）→ 容器 crash-loop。
#
# 正確的入口：
#   GPU（MinerU + Ollama）：  deploy/Dockerfile        見 deploy/README.md
#   純 CPU（gpt-5.6-luna）：  deploy/cloud/Dockerfile  見 deploy/cloud/README.md
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p media

# Pre-download PaddleOCR models during build by running actual inference
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
RUN python -c "\
from PIL import Image, ImageDraw; \
img = Image.new('RGB', (300, 100), color='white'); \
d = ImageDraw.Draw(img); \
d.text((10, 40), 'test card ocr', fill='black'); \
img.save('/tmp/test_build.png'); \
from paddleocr import PaddleOCR; \
ocr = PaddleOCR(use_angle_cls=True, lang='ch'); \
ocr.ocr('/tmp/test_build.png'); \
import os, glob; \
[print(p) for p in glob.glob('/root/**', recursive=True) if 'paddle' in p.lower() or 'paddlex' in p.lower()] \
" || true

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
