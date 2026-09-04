"""
cloud/services/vision_extractor.py — 用 gpt-5.6-luna 直接讀名片照片

結構刻意對齊 app/services/llm_parser.py，讓兩條管線讀起來像兄弟：
模組層 SYSTEM_PROMPT 常數 + 純函式 build_*_content()（可離線單元測試）
+ 一個薄 client 類別 + openai 在方法內 lazy import
（純函式測試不需要套件、不需要 API key，同 llm_parser 對 ollama 的處理）。

只負責「圖片 → 結構化欄位 + 轉錄」；找候選在 app/services/candidate_extractor，
驗證在 app/services/validator，流程串接在 cloud/services/card_pipeline。
"""

import json
import logging
import time

from cloud import config
from cloud.schemas.vision import CardVisionResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You read a photograph of a business card and return structured contact information.

Return two things:
- raw_text: a faithful, line-by-line transcription of every piece of text visible on the card,
  in reading order, one line per visual line. Include labels (Tel/Fax/M/手機/傳真). Do not
  translate, romanize, reformat, or convert between Simplified and Traditional Chinese —
  transcribe exactly what is printed.
- fields: the structured contact fields, taken only from what you transcribed.

Rules:
1. Only use information visible on the card. Never invent, complete or normalize data.
2. Return null for any field you cannot determine. Do not guess.
3. Preserve names, company names and job titles exactly as written on the card, in the
   original script (Traditional Chinese, Simplified Chinese, Japanese, Korean, English).
4. Distinguish phone / mobile / fax using nearby labels (Tel/T/電話, Mobile/M/手機/携帯, Fax/F/傳真) and layout.
5. Copy phone numbers, emails and URLs character-for-character as printed, including the
   separators and brackets used on the card (e.g. keep "（04）2326-2888", do not rewrite it as +886-4-...).
6. The card may be in Traditional/Simplified Chinese, English, Japanese, Korean, or mixed languages.
7. The photo may show the card at an angle, cropped, or against a background. Read only the
   card itself — ignore the table surface, fingers and any other object.
Return every field key; use null when a field is absent."""

_USER_TEXT = "Transcribe this business card and extract the contact fields."

_REPAIR_TEXT = (
    "Your previous extraction failed deterministic validation. Look at the card again.\n"
    "Either correct the field, OR correct raw_text if your transcription omitted or mangled "
    "the value you reported. Do not invent information. Return the full object."
)


# ── 錯誤型別與映射 ────────────────────────────────────────────────────────────
class VisionExtractionError(Exception):
    """帶 HTTP 狀態碼的擷取失敗。

    存在的理由：讓 cloud/routers/cards.py 完全不必 import openai，
    router 只認得這一個型別，錯誤分類全部關在本模組裡。
    """

    def __init__(self, status_code: int, detail: str, retry_after=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after


def check_response(resp) -> None:
    """純函式：檢查 refusal / incomplete / 解析失敗。有問題就丟 VisionExtractionError。"""
    # incomplete：多半是 max_output_tokens 被吃光，detail 直接指出解法
    if getattr(resp, "status", None) == "incomplete":
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        if reason == "max_output_tokens":
            raise VisionExtractionError(
                422,
                "vision response truncated (max_output_tokens); raise CLOUD_MAX_OUTPUT_TOKENS",
            )
        raise VisionExtractionError(422, f"vision response incomplete: {reason}")

    # refusal：模型拒絕處理這張圖
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                refusal = getattr(part, "refusal", "")
                logger.warning("Vision model refused: %s", refusal)
                raise VisionExtractionError(422, "vision model refused to process this image")

    if getattr(resp, "output_parsed", None) is None:
        raise VisionExtractionError(422, "vision response did not match the expected schema")


def map_provider_error(exc: Exception) -> VisionExtractionError:
    """純函式：openai 例外 → HTTP 狀態碼。

    分類原則：金鑰／權限問題是「我方設定錯」不是呼叫端錯，所以是 5xx 不是 4xx；
    429 也回 503（我方暫時不可用），因為超額的是我們的帳號、不是呼叫端。
    圖片本身不被接受（400）才回 422，對齊現有 app/ 的「OCR processing failed」語意。
    """
    import openai

    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        logger.error("Vision provider rejected our credentials (%s)", type(exc).__name__)
        return VisionExtractionError(502, "vision provider authentication failed")
    if isinstance(exc, openai.RateLimitError):
        return VisionExtractionError(503, "vision provider rate limited", retry_after=30)
    if isinstance(exc, openai.APITimeoutError):
        return VisionExtractionError(504, "vision provider timed out")
    if isinstance(exc, openai.APIConnectionError):
        return VisionExtractionError(503, "cannot reach vision provider")
    if isinstance(exc, openai.BadRequestError):
        return VisionExtractionError(422, f"vision provider rejected the image: {exc}")
    if isinstance(exc, openai.InternalServerError):
        return VisionExtractionError(502, "vision provider internal error")
    if isinstance(exc, openai.APIStatusError):
        return VisionExtractionError(502, f"vision provider error (HTTP {exc.status_code})")
    logger.exception("Unexpected vision provider failure")
    return VisionExtractionError(502, "unexpected vision provider failure")


# ── prompt 建構（純函式，離線可測）────────────────────────────────────────────
def build_user_content(data_url: str, detail: str) -> list:
    """一個 input_text + 一個 input_image。

    detail 必須每次明確傳入——SDK 的 ResponseInputImageParam.detail 是 Required。
    """
    return [
        {"type": "input_text", "text": _USER_TEXT},
        {"type": "input_image", "image_url": data_url, "detail": detail},
    ]


def build_repair_content(
    data_url: str, previous: dict, issues: list, raw_text: str, candidates: dict, detail: str
) -> list:
    """修正用的 content。

    與 llm_parser.build_repair_prompt() 最關鍵的差異：**圖片會一起重送**。
    GPU 管線的 repair 只能靠文字（模型從沒看過圖），這裡模型可以真的再看一眼，
    修的是根因而不是搬字。而且共用前綴（instructions + 同一張圖）會命中 prompt cache
    （$0.02/1M），重送幾乎免費。
    """
    error_lines = "\n".join(f"- {i['field']}: {i['message']}" for i in issues)
    text = (
        f"{_REPAIR_TEXT}\n\n"
        f"PREVIOUS FIELDS:\n{json.dumps(previous, ensure_ascii=False)}\n\n"
        f"PREVIOUS raw_text:\n{raw_text}\n\n"
        f"REGEX CANDIDATES FOUND IN YOUR TRANSCRIPTION:\n"
        f"{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"VALIDATION ERRORS:\n{error_lines}"
    )
    return [
        {"type": "input_text", "text": text},
        {"type": "input_image", "image_url": data_url, "detail": detail},
    ]


# ── client ───────────────────────────────────────────────────────────────────
_client = None


def get_client():
    """模組層快取單一 AsyncOpenAI。

    每個 request 建新 client 等於每張名片重做一次 TLS handshake。
    改 env（模型、timeout、base_url）需重啟 server 才會生效。
    """
    global _client
    if _client is None:
        from openai import AsyncOpenAI  # lazy：純函式測試不需要套件

        _client = AsyncOpenAI(
            api_key=config.api_key(),
            base_url=config.base_url(),
            timeout=config.openai_timeout(),
            max_retries=config.openai_max_retries(),  # SDK 內建指數退避 + jitter
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


class VisionCardExtractor:
    """封裝 Responses API 呼叫。extract / repair 兩個動作共用同一條 _respond。"""

    def __init__(self, model=None, detail=None, reasoning_effort=None,
                 max_output_tokens=None, store=None):
        self.model = model or config.vision_model()
        self.detail = detail or config.image_detail()
        self.reasoning_effort = reasoning_effort or config.reasoning_effort()
        self.max_output_tokens = max_output_tokens or config.max_output_tokens()
        self.store = config.store_responses() if store is None else store
        self.last_usage: dict = {}

    async def _respond(self, content: list, cache_key=None) -> CardVisionResult:
        if not config.provider_configured():
            raise VisionExtractionError(503, "vision provider not configured (OPENAI_API_KEY unset)")

        client = get_client()
        started = time.perf_counter()
        try:
            resp = await client.responses.parse(
                model=self.model,
                instructions=SYSTEM_PROMPT,
                input=[{"role": "user", "content": content}],
                text_format=CardVisionResult,
                reasoning={"effort": self.reasoning_effort},
                max_output_tokens=self.max_output_tokens,
                store=self.store,             # 隱私：預設不留存在 provider 端
                prompt_cache_key=cache_key,   # 同一張卡的 extract/repair 共用快取前綴
            )
        except VisionExtractionError:
            raise
        except Exception as exc:
            raise map_provider_error(exc) from exc

        elapsed = time.perf_counter() - started
        self.last_usage = _usage_dict(resp)
        # 記帳 log：這是唯一能拿實際數據去驗證成本模型的途徑。
        # 刻意不記 raw_text / email / 電話 / 地址——PII 不進 log。
        logger.info(
            "Vision extract done: model=%s effort=%s in=%d cached=%d out=%d reasoning=%d time=%.2fs",
            self.model, self.reasoning_effort,
            self.last_usage.get("input_tokens", 0), self.last_usage.get("cached_tokens", 0),
            self.last_usage.get("output_tokens", 0), self.last_usage.get("reasoning_tokens", 0),
            elapsed,
        )

        check_response(resp)
        return resp.output_parsed

    async def extract(self, prepared, cache_key=None) -> CardVisionResult:
        return await self._respond(build_user_content(prepared.data_url, self.detail), cache_key)

    async def repair(self, prepared, previous: dict, issues: list, raw_text: str,
                     candidates: dict, cache_key=None) -> CardVisionResult:
        return await self._respond(
            build_repair_content(prepared.data_url, previous, issues, raw_text,
                                 candidates, self.detail),
            cache_key,
        )


def _usage_dict(resp) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "total_tokens": getattr(u, "total_tokens", 0) or 0,
        "cached_tokens": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0,
        "reasoning_tokens": getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0,
    }
