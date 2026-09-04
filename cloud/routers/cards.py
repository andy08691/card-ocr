"""
cloud/routers/cards.py — 雲端管線的名片 API 路由

端點、method、回應 model 與 app/routers/cards.py **完全相同**，前端只要換 base URL：
  POST /api/cards/upload  — 上傳名片照片，走雲端 VLM 管線，儲存至 DB
  GET  /api/cards/{id}    — 依 ID 查詢
  PUT  /api/cards/{id}    — 修正解析欄位

主要流程（upload），與 app/ 版有一處刻意調序：
  1. 大小 / MIME 檢查            → 413 / 400
  2. 解碼 + 前處理（threadpool） → 400（無法解碼）    ← 先驗圖、後落地
  3. 存原檔到 media/cloud/{uuid}.{ext}（HEIC 例外：存全解析度 JPEG 轉檔）
  4. run_cloud_pipeline()        → 失敗刪圖 + 依錯誤映射狀態碼
  5. 寫入 cards 資料表
  6. 回傳 CardResponse（+ X-Card-Validation-Issues header）

步驟 2 早於 3 是唯一與 app/ 不同的順序：損毀的上傳根本不會產生檔案，
孤兒視窗縮小，「開不了的圖」變成零磁碟 I/O 的乾淨 400。API 契約上看不出差別。
步驟 3 之後任何失敗，都比照 app/ 刪檔。
"""

import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.card import Card
from app.schemas.card import CardResponse, CardUpdate
from cloud import config
from cloud.services import image_prep
from cloud.services.card_pipeline import run_cloud_pipeline
from cloud.services.vision_extractor import VisionExtractionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["cards"])

# 基礎允許類型與 app/ 一致；HEIC 另行動態加入（見 _allowed_content_types）
_BASE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
_HEIC_CONTENT_TYPES = {"image/heic", "image/heif", "image/heic-sequence"}
# 手機與部分 HTTP client 會把 HEIC 標成這些含糊的型別
_AMBIGUOUS_CONTENT_TYPES = {"application/octet-stream", "binary/octet-stream", "", None}


def _allowed_content_types() -> set:
    """動態組允許清單。

    HEIC 只在「pillow-heif 裝得起來」且「CLOUD_ALLOW_HEIC=true」時才對外宣告——
    宣告一個容器解不了的 MIME，會把乾淨的 400 變成解碼階段的 500。
    """
    allowed = set(_BASE_CONTENT_TYPES)
    if image_prep.HEIC_SUPPORTED and config.allow_heic():
        allowed |= _HEIC_CONTENT_TYPES
    return allowed


# 存檔副檔名一律由「PIL 實際偵測到的格式」決定，不信任使用者傳來的檔名。
# 上傳的 content-type 與檔名都可能說謊（手機常把 HEIC 標成 octet-stream），
# 而 /media 是 StaticFiles——它靠副檔名猜 MIME，副檔名錯了瀏覽器就渲染不出來。
_EXT_BY_FORMAT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}


def _archive_extension(source_format) -> str:
    return _EXT_BY_FORMAT.get((source_format or "").upper(), "jpg")


def _media_dir() -> str:
    """雲端圖片的專用目錄，與 app/ 的 media/ 根目錄分開（保留天數清理只掃這裡）。"""
    return os.path.join(config.media_dir(), config.media_subdir())


@router.post("/upload", response_model=CardResponse)
async def upload_card(
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上傳名片照片，用 gpt-5.6-luna 做辨識與欄位擷取。"""
    # ── 1. 大小 / MIME 檢查 ──────────────────────────────────────────────────
    contents = await file.read()
    if len(contents) > config.max_upload_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(contents)} bytes (limit {config.max_upload_bytes()})",
        )

    ctype = file.content_type
    allowed = _allowed_content_types()
    ambiguous = config.sniff_content_type() and ctype in _AMBIGUOUS_CONTENT_TYPES
    if ctype not in allowed and not ambiguous:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ctype}. Allowed: {', '.join(sorted(allowed))}",
        )

    # ── 2. 解碼 + 前處理（先驗圖、後落地）────────────────────────────────────
    try:
        prepared = await run_in_threadpool(
            image_prep.prepare_image,
            contents,
            max_edge=config.image_max_edge(),
            quality=config.image_jpeg_quality(),
            max_bytes=config.image_max_bytes(),
        )
    except Exception as exc:
        logger.info("Rejected undecodable upload (%s): %s", ctype, exc)
        raise HTTPException(status_code=400, detail=f"Cannot decode image: {exc}")

    # ── 3. 存原檔（全解析度，不是送出去的縮圖）──────────────────────────────
    is_heic = (prepared.source_format or "").upper() in ("HEIF", "HEIC")
    if is_heic:
        # 瀏覽器不渲染 HEIC，前端校正 UI 會顯示破圖 → 存全解析度 JPEG 轉檔。
        # 這是唯一的格式例外，log 讓它看得見。
        archive = await run_in_threadpool(image_prep.to_archive_jpeg, contents)
        ext = "jpg"
        logger.info("HEIC upload converted to full-resolution JPEG for storage")
    else:
        archive = contents
        ext = _archive_extension(prepared.source_format)

    media_dir = _media_dir()
    os.makedirs(media_dir, exist_ok=True)
    filename = f"{uuid.uuid4()}.{ext}"
    image_path = os.path.join(media_dir, filename)
    with open(image_path, "wb") as f:
        f.write(archive)
    logger.info("Received upload: %s (%d bytes) -> %s", file.filename, len(contents), image_path)

    # ── 4. 辨識 + 欄位擷取（失敗時清理圖片）─────────────────────────────────
    try:
        result = await run_cloud_pipeline(prepared, cache_key=filename)
    except VisionExtractionError as exc:
        os.path.exists(image_path) and os.remove(image_path)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers)
    except Exception as exc:
        os.path.exists(image_path) and os.remove(image_path)
        logger.exception("Cloud pipeline failed for %s", image_path)
        raise HTTPException(status_code=502, detail=f"Card extraction failed: {exc}")

    if result.issues and config.strict_validation():
        os.path.exists(image_path) and os.remove(image_path)
        raise HTTPException(
            status_code=422,
            detail="validation failed: " + ", ".join(i["field"] for i in result.issues),
        )

    # ── 5. 寫入資料庫 ────────────────────────────────────────────────────────
    card = Card(
        image_path=image_path,
        raw_text=result.raw_text,
        ocr_confidence=result.ocr_confidence,
        **result.fields,
    )
    db.add(card)
    db.commit()
    db.refresh(card)

    # ── 6. 未解決的 issue 走 header，JSON body 與 app/ 保持位元組相同 ────────
    if result.issues:
        response.headers["X-Card-Validation-Issues"] = ",".join(i["field"] for i in result.issues)

    return card


# ── 以下兩個端點是 app/routers/cards.py 的複製 ────────────────────────────────
# 它們是純 DB CRUD、與辨識引擎無關，本來應該共用。但 app/routers/cards.py 無法 import：
# 它在模組層 `from app.services.ocr import run_ocr`，會拉進 opencc 與 MinerU，
# 雲端映像裡兩者都沒有。既然這次的前提是「app/ 一行都不改」，
# 乾淨的重構（把 CRUD router 從 app/ 拆出來共用）留待日後。
# 這是決定，不是疏漏。

@router.get("/{card_id}", response_model=CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db)):
    """依 ID 查詢已處理的名片記錄。"""
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.put("/{card_id}", response_model=CardResponse)
def update_card(card_id: int, updates: CardUpdate, db: Session = Depends(get_db)):
    """修正指定名片的解析欄位（前端人工校正用）。"""
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(card, field, value)

    db.commit()
    db.refresh(card)
    return card
