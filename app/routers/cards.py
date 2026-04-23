"""
routers/cards.py — 名片相關 API 路由

端點：
  POST /api/cards/upload  — 上傳名片圖片，執行 OCR + 解析，儲存至 DB
  GET  /api/cards/{id}    — 依 ID 查詢已處理的名片
  PUT  /api/cards/{id}    — 修正指定名片的解析欄位（前端人工校正用）

主要流程（upload）：
  1. 驗證檔案類型（jpg / png / webp）
  2. 以 UUID 命名儲存至 media/ 目錄
  3. 呼叫 run_ocr()（在 thread pool 執行，避免 blocking event loop）
     → 大圖自動縮圖（>3000px）、語言偵測、PaddleOCR 識別
  4. 呼叫 parse_card() 解析結構化欄位
  5. 寫入 SQLite，回傳 CardResponse
  6. 若 OCR/parse 失敗，刪除已儲存圖片並回傳 HTTP 422
"""

import logging
import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.card import Card
from app.schemas.card import CardResponse, CardUpdate
from app.services.ocr import run_ocr
from app.services.parser import parse_card

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["cards"])

# 允許的上傳檔案類型（MIME type）
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}

# 圖片儲存目錄，預設 media/，可在 .env 的 MEDIA_DIR 覆寫
MEDIA_DIR = os.getenv("MEDIA_DIR", "media")


@router.post("/upload", response_model=CardResponse)
async def upload_card(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上傳名片圖片，執行 OCR 與欄位解析。

    - 圖片以 UUID 重新命名後存入 media/ 目錄
    - OCR 在 thread pool 中執行（PaddleOCR 為 blocking I/O）
    - 失敗時自動刪除已上傳的圖片並回傳 422
    """
    # ── 1. 驗證檔案類型 ──────────────────────────────────────────────────────
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: jpg, png, webp",
        )

    # ── 2. 儲存圖片 ──────────────────────────────────────────────────────────
    # 保留原始副檔名，以 UUID 命名避免檔名衝突
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4()}.{ext}"
    image_path = os.path.join(MEDIA_DIR, filename)
    os.makedirs(MEDIA_DIR, exist_ok=True)

    contents = await file.read()
    logger.info("Received upload: %s (%d bytes)", file.filename, len(contents))
    with open(image_path, "wb") as f:
        f.write(contents)

    # ── 3 & 4. OCR + 解析（失敗時清理圖片）─────────────────────────────────
    try:
        # run_in_threadpool：將 blocking 的 PaddleOCR 呼叫移到 thread pool，
        # 避免 block FastAPI 的 async event loop
        ocr_result = await run_in_threadpool(run_ocr, image_path)
        parsed = parse_card(
            raw_text=ocr_result["raw_text"],
            boxes=ocr_result["boxes"],
            lang=ocr_result["lang"],
        )
    except Exception as exc:
        # 失敗時刪除已儲存的圖片，避免孤兒檔案堆積
        if os.path.exists(image_path):
            os.remove(image_path)
        logger.exception("OCR/parse failed for %s", image_path)
        raise HTTPException(status_code=422, detail=f"OCR processing failed: {exc}")

    # ── 5. 寫入資料庫 ────────────────────────────────────────────────────────
    card = Card(
        image_path=image_path,
        raw_text=ocr_result["raw_text"],
        ocr_confidence=ocr_result["ocr_confidence"],
        **parsed,  # 展開 parse_card() 回傳的所有欄位
    )
    db.add(card)
    db.commit()
    db.refresh(card)  # 讓 card 物件取得 DB 填入的 id 與 created_at

    return card


@router.get("/{card_id}", response_model=CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db)):
    """依 ID 查詢已處理的名片記錄。"""
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.put("/{card_id}", response_model=CardResponse)
def update_card(card_id: int, updates: CardUpdate, db: Session = Depends(get_db)):
    """修正指定名片的解析欄位（前端人工校正用）。

    只更新 request body 中有傳入的欄位（exclude_unset=True），
    未傳入的欄位維持原值不變。
    """
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(card, field, value)

    db.commit()
    db.refresh(card)
    return card
