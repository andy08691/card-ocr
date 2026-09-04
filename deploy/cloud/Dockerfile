# ─────────────────────────────────────────────────────────────────────────────
# deploy/cloud/Dockerfile — 純 CPU 部署映像（雲端 VLM 管線）
#
# 無 GPU、無 MinerU、無 Ollama、無 vLLM。與 deploy/Dockerfile（GPU 版，CUDA base、
# 數 GB）互為替代：這一份只需要一台普通 VPS，約 250MB。
#
# 只 COPY「不需要 MinerU / opencc 就能 import」的 app/ 模組——刻意逐檔列出而非
# COPY app/：這份清單就是雲端管線對 app/ 的依賴邊界文件，而下面的 import 煙霧測試
# 會在 build 當下就把清單漏掉的東西炸出來，不會拖到上線才發現。
#
# 從專案根目錄建置：
#     docker build -f deploy/cloud/Dockerfile -t card-ocr-cloud .
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY deploy/cloud/requirements.txt ./requirements.cloud.txt
RUN pip install --no-cache-dir -r requirements.cloud.txt

# ── 雲端管線本體 ─────────────────────────────────────────────────────────────
COPY cloud ./cloud

# ── app/ 的可重用部分（10 欄位定義、DB、regex 候選、validator、snap）─────────
COPY app/__init__.py app/database.py ./app/
COPY app/models ./app/models
COPY app/schemas ./app/schemas
COPY app/services/__init__.py \
     app/services/parser.py \
     app/services/candidate_extractor.py \
     app/services/validator.py \
     app/services/llm_parser.py \
     app/services/card_extractor.py \
     ./app/services/
# 註：llm_parser.py 必須帶上——card_extractor 在模組層 import 它。
#     但它的 `ollama` 是函式內 lazy import，所以不必安裝 ollama 套件。
#     ocr.py 刻意不帶：它在模組層 import opencc / PIL 並拉進 MinerU。

# ── 煙霧測試：import 解析不了就讓 build 失敗 ────────────────────────────────
# 刻意不 import cloud.main——那會執行 Base.metadata.create_all 與 os.makedirs，
# 把一個空的 card_ocr.db 烤進映像層。import app.database 本身是安全的
# （create_engine 不會連線）。
RUN python -c "import cloud.routers.cards, cloud.services.card_pipeline, \
cloud.services.vision_extractor, cloud.services.image_prep; print('cloud imports OK')"

# ── 再驗一次「無 GPU 依賴」的宣稱 ────────────────────────────────────────────
RUN python -c "import cloud.services.card_pipeline, sys; \
bad={m for m in sys.modules if m.split('.')[0] in {'mineru','opencc','torch','ollama'}}; \
assert not bad, bad; print('no GPU deps OK')"

RUN mkdir -p media/cloud

ENV CLOUD_HOST=0.0.0.0 \
    CLOUD_PORT=8100 \
    MEDIA_DIR=media \
    DATABASE_URL=sqlite:////data/card_ocr.db

EXPOSE 8100
CMD ["sh", "-c", "uvicorn cloud.main:app --host 0.0.0.0 --port ${CLOUD_PORT:-8100} --workers 1"]
