"""
main.py — FastAPI 應用程式入口

負責：
  1. 設定 logging（INFO 層級，含時間戳記）
  2. 建立 FastAPI 實例
  3. 掛載 CORS middleware（允許所有來源，server-to-server 使用場景）
  4. 掛載 /media 靜態目錄（供外部直接存取已上傳的名片圖片）
  5. 掛載 /api/cards 路由（實際業務邏輯在 routers/cards.py）
  6. 提供 /health 端點供監控系統使用

啟動指令：
  開發模式：uvicorn app.main:app --reload
  正式環境：bash start.sh（或 start.bat on Windows）

模型預熱：
  MinerU 首次辨識需載入數 GB 模型。啟動時以背景 thread 預先跑一次 warmup，
  server 立即可對外服務（/health 等），模型在背景載入後即快取於進程內，
  之後的請求不必再付出載入成本。
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os

# 讀取 .env 設定（HOST、PORT、MEDIA_DIR、DATABASE_URL）
load_dotenv()

# 全域 logging 設定，格式：時間 等級 模組名稱 — 訊息
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from app.database import Base, engine, get_db
from app.routers.cards import router as cards_router
from app.services import ocr

# 應用程式啟動時自動建立資料表（若已存在則略過）
# 注意：不會自動新增欄位，升級版本時需手動 ALTER TABLE
Base.metadata.create_all(bind=engine)


def _warmup_models():
    """背景預熱：先 MinerU，再本地 LLM（若走 llm 路徑），讓第一張上傳不付載入成本。"""
    ocr.warmup()
    if os.getenv("CARD_EXTRACTOR", "llm").lower() != "regex":
        from app.services import llm_parser
        llm_parser.warmup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時於背景 thread 預熱模型（非阻塞、失敗不影響啟動）。"""
    threading.Thread(target=_warmup_models, name="warmup", daemon=True).start()
    yield


app = FastAPI(title="Business Card OCR API", version="1.0.0", lifespan=lifespan)

# CORS：允許所有來源，適用於 server-to-server 內部部署
# 若要限制來源，將 allow_origins=["*"] 改為指定網域清單
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 靜態檔案：/media/{filename} 可直接存取已上傳的名片圖片
media_dir = os.getenv("MEDIA_DIR", "media")
os.makedirs(media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

# 掛載名片相關 API 路由（prefix: /api/cards）
app.include_router(cards_router)


@app.get("/")
def root():
    """簡易確認 server 是否在線。"""
    return {"message": "Business Card OCR API is running"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """健康檢查端點，同時確認資料庫連線正常。

    監控系統可定期呼叫此端點（如每 30 秒）。
    回傳 200：{"status": "ok", "db": "ok"}
    回傳 503：資料庫連線失敗
    """
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
