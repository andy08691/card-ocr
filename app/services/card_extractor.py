"""
services/card_extractor.py — LLM 欄位擷取的 workflow controller

流程：candidate（regex 找候選）→ LLM parse（分類）→ validator（確定性驗證）
      → 若有 issue 則 LLM repair（最多 1 次）→ snap 候選型欄位 → 回傳 10-key dict。

設計重點：
  - 回傳值刻意與 parser.parse_card 相同（10 個欄位的 dict），因此 cards.py 的
    Card(**parsed) 完全不用動。
  - 任何 Ollama / LLM 例外都會 fallback 到 regex parse_card，確保 server 不會因為
    LLM 掛掉就整張回 422（OCR 成功不該被分類失敗拖垮）。
  - 環境變數 CARD_EXTRACTOR=regex 可一鍵回到舊的純 regex 路徑（方便 A/B 與回滾）。
"""

import logging
import os
import re

from app.schemas.extraction import CardCandidates
from app.services.candidate_extractor import extract_candidates
from app.services.llm_parser import LocalLLMParser, compact_boxes
from app.services.parser import parse_card
from app.services.validator import validate_card

logger = logging.getLogger(__name__)

_MAX_REPAIR = 1


def _snap_candidates(d: dict, candidates: CardCandidates) -> dict:
    """把候選型欄位 snap 回確定性候選的表面字串。

    LLM 可能把電話重新格式化（去括號、改分隔符），這裡以正規化比對後，
    用 regex 找到的原始表面字串取代，確保 DB 存的是乾淨一致的值。
    """
    def norm_phone(s):
        return re.sub(r"\D", "", s or "")

    def norm_generic(s):
        return re.sub(r"[\s\W_]+", "", (s or "").lower())

    plan = (
        ("email", candidates.emails, norm_generic),
        ("website", candidates.websites, norm_generic),
        ("phone", candidates.phones, norm_phone),
        ("mobile", candidates.phones, norm_phone),
        ("fax", candidates.phones, norm_phone),
    )
    for field, pool, norm in plan:
        if not d.get(field):
            continue
        v = norm(d[field])
        for c in pool:
            if norm(c) == v:
                d[field] = c
                break
    return d


async def extract_card(raw_text: str, boxes: list, lang: str) -> dict:
    """回傳與 parse_card 相同結構的 10-key dict。LLM 失敗時 fallback 到 regex。"""
    if os.getenv("CARD_EXTRACTOR", "llm").lower() == "regex":
        return parse_card(raw_text, boxes, lang)

    try:
        candidates = extract_candidates(raw_text)
        cand_dict = candidates.model_dump()
        layout = compact_boxes(boxes)
        llm = LocalLLMParser()

        result = await llm.parse(raw_text, lang, cand_dict, layout)
        issues = validate_card(result, raw_text, candidates)

        repairs = 0
        while issues and repairs < _MAX_REPAIR:
            repairs += 1
            logger.info("LLM extraction issues, repairing: %s", [i.field for i in issues])
            result = await llm.repair(
                result.model_dump(),
                [i.model_dump() for i in issues],
                raw_text,
                cand_dict,
            )
            issues = validate_card(result, raw_text, candidates)

        if issues:
            logger.warning(
                "LLM extraction still has issues after repair (saving best-effort): %s",
                [(i.field, i.message) for i in issues],
            )

        return _snap_candidates(result.model_dump(), candidates)

    except Exception:
        logger.exception("LLM extraction failed; falling back to regex parser")
        return parse_card(raw_text, boxes, lang)
