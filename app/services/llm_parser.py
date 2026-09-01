"""
services/llm_parser.py — 本地 Ollama LLM 做名片欄位「分類」

- 用 Ollama 的 structured output：把 CardExtraction 的 JSON schema 當成 format 傳給模型，
  並設 temperature=0，讓輸出穩定且必為合法 JSON（不是靠 prompt 拜託模型回 JSON）。
- 模型 / host / keep_alive 皆可用環境變數覆寫（預設 qwen3:4b、localhost:11434、5m）。
- ollama 套件採 lazy import（在呼叫內），這樣純函式（build_prompt / compact_boxes）
  的單元測試不需要安裝套件或啟動 server。

只負責「文字 → 結構化欄位」；找候選在 candidate_extractor、驗證在 validator、
流程串接在 card_extractor。
"""

import json
import logging
import os

from app.schemas.extraction import CardExtraction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract structured contact information from business-card OCR text.

Rules:
1. Only use information present in the provided OCR text. Never invent data.
2. Return null for any field you cannot determine.
3. Preserve names, company names and job titles exactly as written on the card.
4. Distinguish phone / mobile / fax using nearby labels (Tel/T, Mobile/M/手機/携帯, Fax/F/傳真) and layout.
5. For email, phone, mobile, fax and website, choose values from the provided CANDIDATES when candidates are given.
6. The card may be in Traditional/Simplified Chinese, English, Japanese, Korean, or mixed languages.
Return every field key; use null when a field is absent."""


def _env_model() -> str:
    return os.getenv("OLLAMA_MODEL", "qwen3:4b")


def _env_host() -> str:
    return os.getenv("OLLAMA_HOST", "http://localhost:11434")


def _env_keep_alive() -> str:
    return os.getenv("OLLAMA_KEEP_ALIVE", "5m")


def _env_think() -> bool:
    # 對「結構化欄位擷取」關閉 thinking：qwen3 等模型預設會先產生大量 <think> 推理，
    # 與 structured-output grammar 衝突且極慢。預設 false；OLLAMA_THINK=true 可開啟。
    return os.getenv("OLLAMA_THINK", "false").lower() in ("1", "true", "yes", "on")


_EXTRACTION_SCHEMA = None


def _extraction_schema() -> dict:
    """CardExtraction 的 JSON schema，但把 10 個欄位全標成 required。

    CardBase 欄位帶預設值 → 預設不是 required，grammar 會允許模型吐空物件 `{}`。
    這裡強制全部 required（仍可為 null），逼模型每欄都輸出，大幅改善小模型的擷取率。
    """
    global _EXTRACTION_SCHEMA
    if _EXTRACTION_SCHEMA is None:
        schema = CardExtraction.model_json_schema()
        schema["required"] = list(schema.get("properties", {}).keys())
        _EXTRACTION_SCHEMA = schema
    return _EXTRACTION_SCHEMA


def compact_boxes(boxes: list) -> list:
    """把 MinerU 的 boxes 壓成 [{i, x, y, text}]，讓 LLM 理解版面分組又省 token。

    只取每個文字塊的 line index、左上角 (x, y) 與文字，不倒整個多邊形座標。
    """
    out: list = []
    for i, b in enumerate(boxes or []):
        text = (b.get("text") or "").strip()
        if not text:
            continue
        try:
            xs = [p[0] for p in b["bbox"]]
            ys = [p[1] for p in b["bbox"]]
            x, y = int(min(xs)), int(min(ys))
        except Exception:
            x, y = 0, i
        out.append({"i": i, "x": x, "y": y, "text": text})
    return out


def build_prompt(raw_text: str, lang: str, candidates: dict, layout: list) -> str:
    """組出擷取用的 user prompt（純函式，可單元測試）。"""
    return (
        f"LANGUAGE: {lang}\n\n"
        f"OCR TEXT:\n{raw_text}\n\n"
        f"LAYOUT (line index / x / y / text):\n"
        f"{json.dumps(layout, ensure_ascii=False)}\n\n"
        f"CANDIDATES (regex-extracted; pick from these for email/phone/website):\n"
        f"{json.dumps(candidates, ensure_ascii=False, indent=2)}\n\n"
        f"Extract the contact fields as JSON."
    )


def build_repair_prompt(previous: dict, issues: list, raw_text: str, candidates: dict) -> str:
    """組出修正用的 user prompt（純函式，可單元測試）。只叫模型修 issue 指到的欄位。"""
    error_lines = "\n".join(f"- {i['field']}: {i['message']}" for i in issues)
    return (
        "Your previous extraction contained validation errors.\n\n"
        f"OCR TEXT:\n{raw_text}\n\n"
        f"CANDIDATES:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"PREVIOUS RESULT:\n{json.dumps(previous, ensure_ascii=False)}\n\n"
        f"VALIDATION ERRORS:\n{error_lines}\n\n"
        "Correct ONLY the fields necessary to resolve these errors. "
        "Do not invent information. Return the full JSON with every field key."
    )


class LocalLLMParser:
    """封裝 Ollama chat 呼叫。"""

    def __init__(self, model: str = None, host: str = None, keep_alive: str = None, think: bool = None):
        self.model = model or _env_model()
        self.host = host or _env_host()
        self.keep_alive = keep_alive or _env_keep_alive()
        self.think = _env_think() if think is None else think

    async def _chat(self, prompt: str) -> CardExtraction:
        from ollama import AsyncClient  # lazy：純函式測試不需套件/server

        client = AsyncClient(host=self.host)
        resp = await client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            format=_extraction_schema(),
            options={"temperature": 0},
            keep_alive=self.keep_alive,
            think=self.think,
        )
        return CardExtraction.model_validate_json(resp["message"]["content"])

    async def parse(self, raw_text: str, lang: str, candidates: dict, layout: list) -> CardExtraction:
        return await self._chat(build_prompt(raw_text, lang, candidates, layout))

    async def repair(self, previous: dict, issues: list, raw_text: str, candidates: dict) -> CardExtraction:
        return await self._chat(build_repair_prompt(previous, issues, raw_text, candidates))


def warmup() -> None:
    """啟動時預先載入 LLM 模型（sync 呼叫），避免第一個上傳付模型載入成本。

    送一個極短 chat（num_predict=1），只為觸發模型載入並依 keep_alive 常駐。
    設計為非致命：失敗只記 log，不影響 server 啟動。
    """
    try:
        import ollama  # lazy

        client = ollama.Client(host=_env_host())
        client.chat(
            model=_env_model(),
            messages=[{"role": "user", "content": "ok"}],
            options={"temperature": 0, "num_predict": 1},
            keep_alive=_env_keep_alive(),
            think=_env_think(),
        )
        logger.info("Ollama warmup done (model=%s, keep_alive=%s)", _env_model(), _env_keep_alive())
    except Exception:
        logger.exception("Ollama warmup failed (non-fatal); model will load on first request")
