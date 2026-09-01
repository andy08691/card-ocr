"""
services/candidate_extractor.py — 用 regex「找候選」，不做欄位分類

設計原則（見計畫）：regex 可靠地負責「找出哪些字串長得像 email / 電話 / 網址」，
但**不**決定「哪個電話是 mobile、哪個是 fax」——那個判斷交給 LLM 依標籤與版面處理，
因為不同國家格式差異太大，用 regex 硬分類正是舊 parser 在非標準名片失準的來源。

重用 parser.py 既有的 compiled regex，避免重複維護樣式。
"""

import re

from app.schemas.extraction import CardCandidates
from app.services.parser import (
    EMAIL_RE,
    WEBSITE_RE,
    MOBILE_TW_RE,
    PHONE_TW_RE,
    PHONE_INTL_RE,
    PHONE_FREE_RE,
    EN_TEL_LABEL_RE,
    EN_MOBILE_LABEL_RE,
    EN_FAX_LABEL_RE,
    EN_PHONE_GENERAL_RE,
)


def _dedup_preserve(items: list) -> list:
    """去重並保留首次出現順序（去頭尾空白）。"""
    seen, out = set(), []
    for x in items:
        x = (x or "").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _dedup_phones(items: list) -> list:
    """以「純數字」為 key 去重，保留第一個出現的表面字串；過短者視為雜訊丟棄。"""
    seen, out = set(), []
    for x in items:
        x = (x or "").strip()
        if not x:
            continue
        digits = re.sub(r"\D", "", x)
        if len(digits) < 6:
            continue
        if digits in seen:
            continue
        seen.add(digits)
        out.append(x)
    return out


def extract_candidates(raw_text: str) -> CardCandidates:
    """從 OCR 原始文字找出 email / phone / website 候選（不分類）。"""
    emails = _dedup_preserve(EMAIL_RE.findall(raw_text))
    websites = _dedup_preserve(WEBSITE_RE.findall(raw_text))

    phones: list = []
    # 這些樣式沒有 capturing group → findall 回傳整段比對字串
    for rx in (MOBILE_TW_RE, PHONE_TW_RE, PHONE_INTL_RE, PHONE_FREE_RE):
        phones += rx.findall(raw_text)
    # 這些是「標籤 + 號碼」樣式，號碼在 group(1)
    for rx in (EN_TEL_LABEL_RE, EN_MOBILE_LABEL_RE, EN_FAX_LABEL_RE, EN_PHONE_GENERAL_RE):
        phones += [m.group(1) for m in rx.finditer(raw_text)]
    phones = _dedup_phones(phones)

    return CardCandidates(emails=emails, phones=phones, websites=websites)
