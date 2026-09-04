"""
cloud/config.py — 雲端管線的環境變數集中處

⚠️ 必須是 cloud 套件裡「第一個」被 import 的模組。

原因：app/database.py 在 **import 當下** 就 load_dotenv() 讀根目錄 .env、
取 DATABASE_URL 並建立 engine。若 app.database 先被 import，根目錄 .env 的
DATABASE_URL 會勝出，雲端管線就會安靜地寫進 GPU 管線的資料庫。
dotenv 預設 override=False（先載入者勝），所以只要 .env.cloud 先進 os.environ，
根目錄的 .env 就蓋不掉雲端設定。tests/test_cloud_pipeline.py 有測試釘死這個順序。

刻意用「函式」而非模組層常數：讓測試改完 os.environ 後立刻生效
（沿用 app/services/llm_parser.py 的 _env_model() / _env_host() 風格）。
本模組不 import 任何 app.*，維持 app-free。
"""

import os

from dotenv import load_dotenv

# 在任何 app.* 被 import 之前把 .env.cloud 灌進 os.environ
load_dotenv(os.getenv("CLOUD_ENV_FILE", ".env.cloud"))

# ── 清掉「空字串」的第三方 SDK 環境變數 ───────────────────────────────────────
# .env 檔常把選填項寫成 `OPENAI_BASE_URL=`（留空表示不設定），dotenv 會照實把
# 空字串放進 os.environ。問題是 OpenAI SDK 在 base_url=None 時會「自己」去讀這個
# 環境變數，空字串會被當成合法的 base URL → httpx.UnsupportedProtocol
# （"Request URL is missing an 'http://' or 'https://' protocol"），
# 表面症狀卻是 APIConnectionError，很難查。
#
# 同 app/services/ocr.py:_select_backend() 對 MINERU_BACKEND 的處理：空值視為未設定。
# 這裡直接從 os.environ 移除，因為問題出在 SDK 自己讀 env，光靠 config 函式回 None 沒用。
for _name in ("OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_WEBHOOK_SECRET"):
    if not (os.environ.get(_name) or "").strip():
        os.environ.pop(_name, None)


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Server ────────────────────────────────────────────────────────────────────
# 刻意不叫 HOST / PORT：start.sh、start.bat、deploy/entrypoint.sh 都在吃那兩個名字，
# 兩條管線可能看到同一份 .env，撞名會讓其中一邊綁錯埠。
def host() -> str:
    return os.getenv("CLOUD_HOST") or "0.0.0.0"


def port() -> int:
    return _int("CLOUD_PORT", 8100)


# ── Storage & DB（與 app/ 同名，讓 image_path 兩條管線完全一致）──────────────
def media_dir() -> str:
    return os.getenv("MEDIA_DIR") or "media"


def media_subdir() -> str:
    """相對 media_dir() 的子目錄。雲端圖片獨立存放，讓保留天數清理不會誤刪本機管線的圖。"""
    return os.getenv("CLOUD_MEDIA_SUBDIR", "cloud")


def image_retention_days() -> int:
    return _int("CLOUD_IMAGE_RETENTION_DAYS", 0)


# ── Provider ──────────────────────────────────────────────────────────────────
def api_key() -> str:
    return os.getenv("OPENAI_API_KEY") or ""


def provider_configured() -> bool:
    return bool(api_key())


def base_url():
    return os.getenv("OPENAI_BASE_URL") or None


def vision_model() -> str:
    return os.getenv("CLOUD_VISION_MODEL") or "gpt-5.6-luna"


def reasoning_effort() -> str:
    return os.getenv("CLOUD_REASONING_EFFORT") or "low"


def max_output_tokens() -> int:
    return _int("CLOUD_MAX_OUTPUT_TOKENS", 4000)


def openai_timeout() -> float:
    return _float("CLOUD_OPENAI_TIMEOUT", 60.0)


def openai_max_retries() -> int:
    return _int("CLOUD_OPENAI_MAX_RETRIES", 3)


def store_responses() -> bool:
    """隱私預設：不把名片影像與 PII 留在 provider 端。"""
    return _bool("CLOUD_STORE_RESPONSES", False)


# ── Image preprocessing ───────────────────────────────────────────────────────
def image_max_edge() -> int:
    return _int("CLOUD_IMAGE_MAX_EDGE", 2048)


def image_jpeg_quality() -> int:
    return _int("CLOUD_IMAGE_JPEG_QUALITY", 88)


def image_max_bytes() -> int:
    return _int("CLOUD_IMAGE_MAX_BYTES", 4 * 1024 * 1024)


def image_detail() -> str:
    return os.getenv("CLOUD_IMAGE_DETAIL") or "auto"


def allow_heic() -> bool:
    return _bool("CLOUD_ALLOW_HEIC", True)


def sniff_content_type() -> bool:
    return _bool("CLOUD_SNIFF_CONTENT_TYPE", True)


def max_upload_bytes() -> int:
    return _int("CLOUD_MAX_UPLOAD_BYTES", 20 * 1024 * 1024)


# ── Pipeline ──────────────────────────────────────────────────────────────────
def max_repair() -> int:
    return _int("CLOUD_MAX_REPAIR", 1)


def strict_validation() -> bool:
    return _bool("CLOUD_STRICT_VALIDATION", False)
