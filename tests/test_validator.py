"""Unit tests for app/services/validator.py — pure, no LLM/server."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas.extraction import CardCandidates, CardExtraction
from app.services.validator import validate_card


def cand(emails=None, phones=None, websites=None):
    return CardCandidates(emails=emails or [], phones=phones or [], websites=websites or [])


class TestCandidateFields:
    def test_all_valid_no_issues(self):
        ex = CardExtraction(
            email="a@b.com", phone="02-2712-1234", mobile="0912-345-678",
            company_name="ABC 公司",
        )
        c = cand(emails=["a@b.com"], phones=["02-2712-1234", "0912-345-678"])
        raw = "ABC 公司 a@b.com 02-2712-1234 0912-345-678"
        assert validate_card(ex, raw, c) == []

    def test_email_not_in_candidates(self):
        ex = CardExtraction(email="ghost@x.com")
        issues = validate_card(ex, "raw", cand(emails=["real@x.com"]))
        assert any(i.field == "email" for i in issues)

    def test_phone_not_in_candidates(self):
        ex = CardExtraction(phone="09-9999-9999")
        issues = validate_card(ex, "raw", cand(phones=["02-2712-1234"]))
        assert any(i.field == "phone" for i in issues)

    def test_phone_membership_is_normalized(self):
        # LLM 重新格式化過的號碼，正規化後仍屬候選 → 不算 issue
        ex = CardExtraction(fax="(02) 2712 9999")
        assert validate_card(ex, "raw", cand(phones=["02-2712-9999"])) == []


class TestConflicts:
    def test_mobile_fax_same_number(self):
        ex = CardExtraction(mobile="0912345678", fax="0912-345-678")
        c = cand(phones=["0912345678", "0912-345-678"])
        issues = validate_card(ex, "raw", c)
        assert any("duplicates" in i.message for i in issues)


class TestFuzzyFields:
    def test_company_absent_from_ocr_flagged(self):
        ex = CardExtraction(company_name="完全沒出現的公司名")
        issues = validate_card(ex, "這裡是別的文字 ABC", cand())
        assert any(i.field == "company_name" for i in issues)

    def test_company_token_overlap_passes(self):
        ex = CardExtraction(company_name="ABC 科技股份有限公司")
        issues = validate_card(ex, "ABC科技股份有限公司 王小明", cand())
        assert not any(i.field == "company_name" for i in issues)

    def test_person_and_title_never_flagged(self):
        # 模糊型的人名/職稱不做存在檢查（易被 OCR 拆行）
        ex = CardExtraction(person_name="王小明", job_title="業務協理")
        assert validate_card(ex, "完全不同的內容", cand()) == []


class TestNulls:
    def test_all_null_no_issues(self):
        assert validate_card(CardExtraction(), "raw", cand()) == []
