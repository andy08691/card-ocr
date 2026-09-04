"""
cloud/main.py — 雲端管線的 FastAPI 入口（GPU-free）

啟動指令：
  開發：  uvicorn cloud.main:app --reload --port 8100
  正式：  bash start.cloud.sh

與 app/main.py 的差異：
  1. **第一個 import 必須是 cloud.config**——它負責 load_dotenv(".env.cloud")。
     app/database.py 在 import 當下就讀 DATABASE_URL 建 engine，順序錯了
     雲端管線會安靜地寫進 GPU 管線的資料庫。
  2. **沒有 warmup thread**：沒有本機模型要載入，這是實質的簡化。
     lifespan 改成做設定檢查、啟動保留期清理、關閉時收掉 HTTP client。
  3. 綁 CLOUD_HOST / CLOUD_PORT（預設 8100），可與 app/ 的 8000 並行。
"""

# ⚠️ 順序敏感：必須在任何 app.* 之前，讓 .env.cloud 先進 os.environ
from cloud import config  # noqa: F401  (import 的副作用就是 load_dotenv)

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from app.database import Base, engine, get_db
from app.models import card as _card_model  # noqa: F401  明確 import，讓 create_all 看得到 cards 表
from cloud.routers.cards import router as cards_router
from cloud.services import retention
from cloud.services.vision_extractor import close_client

logger = logging.getLogger(__name__)

# 對已存在的表是 no-op；讓 DATABASE_URL 指向全新檔案時可自動建表
# （在全新的共用 DB 上兩個 app 同時首次啟動有極窄的競態，文件註明首次請依序啟動）
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """設定檢查（log 而不 raise）+ 保留期清理；關閉時收掉共用的 HTTP client。"""
    if not config.provider_configured():
        # 刻意 log 而不 raise：比照 app/ 的「warmup 失敗不影響啟動」，
        # 讓 /health 仍答得出來，也避免設定錯就讓容器 crash-loop。
        logger.error(
            "OPENAI_API_KEY not set — /api/cards/upload will return 503 until configured"
        )
    else:
        logger.info(
            "Cloud pipeline ready: model=%s effort=%s max_edge=%d store=%s",
            config.vision_model(), config.reasoning_effort(),
            config.image_max_edge(), config.store_responses(),
        )
    retention.start_background_sweeper()
    yield
    await close_client()


app = FastAPI(
    title="Business Card OCR API (Cloud)",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS：與 app/ 相同，適用 server-to-server 內部部署
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 靜態檔案：掛 MEDIA_DIR 根目錄，雲端圖片走 /media/cloud/xxx.jpg
media_dir = config.media_dir()
os.makedirs(os.path.join(media_dir, config.media_subdir()), exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

app.include_router(cards_router)


@app.get("/")
def root():
    return {"message": "Business Card OCR API (Cloud) is running"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """健康檢查——**絕不呼叫 OpenAI**。

    30 秒一次的監控 = 2,880 次/天，遠超過 100–200 張的真實用量，整個成本模型會倒過來；
    而且 provider 一個瞬斷就會讓容器反覆重啟。這裡只查 DB + 回報零網路的設定事實。
    要真的確認金鑰有效，用下面的 /health/provider（人工觸發，別接進監控）。
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {
        "status": "ok",
        "db": "ok",
        "pipeline": "cloud-vision",
        "model": config.vision_model(),
        "provider_configured": config.provider_configured(),
        "heic_supported": bool(config.allow_heic()) and _heic_available(),
    }


@app.get("/health/provider")
async def health_provider():
    """選配、給 on-call 用：確認金鑰有效且模型取用得到。不耗 token，但**別接進監控或 HEALTHCHECK**。"""
    if not config.provider_configured():
        raise HTTPException(status_code=503, detail="vision provider not configured")
    try:
        from cloud.services.vision_extractor import get_client

        model = await get_client().models.retrieve(config.vision_model())
        return {"status": "ok", "model": getattr(model, "id", config.vision_model())}
    except Exception as exc:
        logger.error("Provider health check failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="vision provider unreachable")


def _heic_available() -> bool:
    from cloud.services.image_prep import HEIC_SUPPORTED

    return HEIC_SUPPORTED
