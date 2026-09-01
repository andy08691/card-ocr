"""
services/validator.py — 對 LLM 擷取結果做確定性驗證

分兩類欄位（見計畫「關鍵設計 #3」）：
  候選型（email / phone / mobile / fax / website）：正規化後必須屬於 regex 候選之一，
    並檢查同一號碼是否被指到多個電話欄位（mobile == fax 之類）。
  模糊型（company / person / english_name / job_title / address）：LLM 常會跨 box 合併、
    修 OCR 黏字，故**不**做嚴格子字串比對；只對 company_name / address 做寬鬆的
    token 重疊檢查（完全找不到任何 token 才提示），避免誤觸發 repair 把對的改壞。

validator 回傳 issue 清單；空清單代表通過。它不修改資料，只描述問題，
repair 由 card_extractor 依這些 issue 導引 LLM 修正。
"""

import re

from pydantic import BaseModel

from app.schemas.extraction import CardCandidates, CardExtraction


class ValidationIssue(BaseModel):
    field: str
    message: str


def _norm_phone(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _norm_generic(s: str) -> str:
    """去空白、標點、大小寫，供文字/網址/email 的寬鬆比對。"""
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _phone_in(value: str, pool: list) -> bool:
    v = _norm_phone(value)
    return bool(v) and any(_norm_phone(c) == v for c in pool)


def _text_in(value: str, pool: list) -> bool:
    v = _norm_generic(value)
    return bool(v) and any(_norm_generic(c) == v for c in pool)


def validate_card(
    extraction: CardExtraction,
    raw_text: str,
    candidates: CardCandidates,
) -> list:
    """回傳 ValidationIssue 清單（空 = 通過）。"""
    issues: list = []
    d = extraction.model_dump()

    # ── 候選型欄位：須屬於候選 ──────────────────────────────────────────────
    if d["email"] and not _text_in(d["email"], candidates.emails):
        issues.append(ValidationIssue(field="email", message="email not among OCR candidates"))
    if d["website"] and not _text_in(d["website"], candidates.websites):
        issues.append(ValidationIssue(field="website", message="website not among OCR candidates"))
    for f in ("phone", "mobile", "fax"):
        if d[f] and not _phone_in(d[f], candidates.phones):
            issues.append(ValidationIssue(field=f, message=f"{f} not among OCR phone candidates"))

    # ── 衝突：同一號碼被指到多個電話欄位 ────────────────────────────────────
    seen: dict = {}
    for f in ("phone", "mobile", "fax"):
        if not d[f]:
            continue
        n = _norm_phone(d[f])
        if n and n in seen:
            issues.append(ValidationIssue(field=f, message=f"{f} duplicates {seen[n]}"))
        elif n:
            seen[n] = f

    # ── 模糊欄位：寬鬆存在檢查（只查 company / address，零 token 重疊才提示）──
    norm_raw = _norm_generic(raw_text)
    for f in ("company_name", "address"):
        val = d[f]
        if not val:
            continue
        if _norm_generic(val) in norm_raw:
            continue
        tokens = [t for t in re.split(r"[\s,]+", val) if len(t) >= 2]
        if tokens and not any(_norm_generic(t) in norm_raw for t in tokens):
            issues.append(ValidationIssue(field=f, message=f"{f} not found in OCR text"))

    return issues
