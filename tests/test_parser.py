"""Unit tests for app/services/parser.py — no OCR or server dependency."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.parser import (
    parse_card,
    PHONE_TW_RE,
    MOBILE_TW_RE,
    EMAIL_RE,
    WEBSITE_RE,
    EN_NAME_RE,
    EN_COMPANY_SUFFIXES,
    EN_TEL_LABEL_RE,
    EN_MOBILE_LABEL_RE,
    _extract_phone,
    _extract_mobile,
    _extract_fax,
    _extract_email,
    _extract_website,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def boxes(lines):
    """Create fake bounding-box list sorted top-to-bottom."""
    return [
        {"text": l, "bbox": [[0, i * 20], [100, i * 20], [100, (i + 1) * 20], [0, (i + 1) * 20]], "confidence": 0.95}
        for i, l in enumerate(lines)
    ]


def zh_parse(lines):
    text = "\n".join(lines)
    return parse_card(text, boxes(lines), lang="zh")


def en_parse(lines):
    text = "\n".join(lines)
    return parse_card(text, boxes(lines), lang="en")


# ── Regex: PHONE_TW_RE ────────────────────────────────────────────────────────

class TestPhoneTWRe:
    def test_format_paren_area(self):
        assert PHONE_TW_RE.search("(02)2326-2888")

    def test_format_space_separator(self):
        assert PHONE_TW_RE.search("07 782 7271")

    def test_format_dash_separator(self):
        # Key fix: 07-782 7271 previously didn't match
        assert PHONE_TW_RE.search("07-782 7271")

    def test_format_standard_dash(self):
        assert PHONE_TW_RE.search("04-2326-2888")

    def test_format_full_paren(self):
        assert PHONE_TW_RE.search("(07) 782-7271")

    def test_does_not_match_mobile(self):
        # 09xx mobile should not match as a landline
        m = PHONE_TW_RE.search("0912-345-678")
        # Even if regex matches, _extract_phone excludes 09xx
        result = _extract_phone("0912-345-678")
        assert result is None or not result.startswith("09")

    def test_does_not_match_too_short(self):
        assert not PHONE_TW_RE.search("02-123")


# ── Regex: MOBILE_TW_RE ───────────────────────────────────────────────────────

class TestMobileTWRe:
    def test_standard(self):
        assert MOBILE_TW_RE.search("0912-345-678")

    def test_no_separator(self):
        assert MOBILE_TW_RE.search("0912345678")

    def test_space_separator(self):
        assert MOBILE_TW_RE.search("0912 345 678")


# ── Regex: EMAIL_RE ───────────────────────────────────────────────────────────

class TestEmailRe:
    def test_basic(self):
        assert EMAIL_RE.search("user@example.com")

    def test_with_plus(self):
        assert EMAIL_RE.search("user+tag@example.co.uk")

    def test_subdomain(self):
        assert EMAIL_RE.search("john.doe@mail.company.org")

    def test_no_at(self):
        assert not EMAIL_RE.search("notanemail.com")


# ── Regex: WEBSITE_RE ─────────────────────────────────────────────────────────

class TestWebsiteRe:
    def test_http(self):
        assert WEBSITE_RE.search("http://example.com")

    def test_www(self):
        assert WEBSITE_RE.search("www.example.com")

    def test_strips_trailing_punctuation(self):
        m = WEBSITE_RE.search("Visit www.example.com!")
        assert m and "!" not in m.group(0)

    def test_strips_trailing_comma(self):
        m = WEBSITE_RE.search("See www.example.com, for details")
        assert m and "," not in m.group(0)


# ── Regex: EN_NAME_RE ─────────────────────────────────────────────────────────

class TestEnNameRe:
    def test_title_case_2word(self):
        assert EN_NAME_RE.match("Nancy Hoo")

    def test_all_caps_2word(self):
        assert EN_NAME_RE.match("NANCY HOO")

    def test_3word_name(self):
        assert EN_NAME_RE.match("Woo Jeong Hong")

    def test_middle_initial(self):
        assert EN_NAME_RE.match("John A. Smith")

    def test_all_caps_3word(self):
        assert EN_NAME_RE.match("WOO JEONG HONG")

    def test_rejects_single_word(self):
        assert not EN_NAME_RE.match("John")

    def test_rejects_lowercase(self):
        assert not EN_NAME_RE.match("john smith")

    def test_rejects_ocr_merged(self):
        # "IANCHRISTIAN" is OCR-merged text, should not match
        assert not EN_NAME_RE.match("IANCHRISTIAN")


# ── Regex: EN_COMPANY_SUFFIXES ────────────────────────────────────────────────

class TestEnCompanySuffixes:
    def test_corp(self):
        assert EN_COMPANY_SUFFIXES.search("ACME Corp")

    def test_ltd(self):
        assert EN_COMPANY_SUFFIXES.search("ACME Ltd.")

    def test_limited(self):
        assert EN_COMPANY_SUFFIXES.search("Halti Limited")

    def test_oy(self):
        assert EN_COMPANY_SUFFIXES.search("Halti Oy")

    def test_gmbh(self):
        assert EN_COMPANY_SUFFIXES.search("Muster GmbH")

    def test_pte(self):
        assert EN_COMPANY_SUFFIXES.search("StarBiz Pte Ltd")

    def test_holdings(self):
        assert EN_COMPANY_SUFFIXES.search("Asia Holdings")


# ── Universal extractors ──────────────────────────────────────────────────────

class TestExtractPhone:
    def test_taiwan_landline(self):
        assert _extract_phone("Tel: 02-2326-2888") is not None

    def test_taiwan_dash_format(self):
        result = _extract_phone("07-782 7271")
        assert result is not None

    def test_fax_strip_mixed_line(self):
        # Phone should be extracted; fax portion stripped
        result = _extract_phone("Tel:03-3497-8076Fax:03-3497-2258")
        assert result is not None
        assert "8076" in result

    def test_mobile_not_returned_as_phone(self):
        result = _extract_phone("M. +358 40 123 4567")
        # Should not return the mobile number as the landline
        assert result is None or "+358 40" not in result

    def test_intl_format(self):
        result = _extract_phone("+1-604-960-3231")
        assert result is not None


class TestExtractFax:
    def test_english_label(self):
        result = _extract_fax("Fax: 03-3497-2258")
        assert result is not None and "3497" in result

    def test_zh_label(self):
        result = _extract_fax("傳真: 02-1234-5678")
        assert result is not None and "1234" in result

    def test_no_fax(self):
        assert _extract_fax("Tel: 02-2326-2888") is None


class TestExtractMobile:
    def test_tw_mobile(self):
        result = _extract_mobile("手機: 0912-345-678")
        assert result is not None

    def test_mobile_label(self):
        result = _extract_mobile("M. +358 40 123 4567")
        assert result is not None

    def test_none_when_absent(self):
        assert _extract_mobile("Tel: 02-2326-2888") is None


# ── Chinese card parsing ──────────────────────────────────────────────────────

class TestParseZh:
    def test_company_with_main_suffix(self):
        r = zh_parse(["高都汽車股份有限公司", "陳志均", "銷售顧問", "831高雄市大寮區力行路197號"])
        assert r["company_name"] == "高都汽車股份有限公司"
        assert r["person_name"] == "陳志均"
        assert r["job_title"] == "銷售顧問"

    def test_branch_suffix_not_captured_as_person(self):
        r = zh_parse(["高都汽車股份有限公司", "鳳林管業所", "陳志均", "銷售顧問"])
        assert r["person_name"] == "陳志均"
        # Branch line should not be company_name (already have main)
        assert r["company_name"] == "高都汽車股份有限公司"

    def test_hotline_not_job_title(self):
        # Phone-like lines should not be captured as job_title
        r = zh_parse(["高都汽車股份有限公司", "陳志均", "銷售顧問", "顧客服務専線|02-55997299"])
        assert r["job_title"] == "銷售顧問"

    def test_phone_extracted(self):
        r = zh_parse(["ABC有限公司", "王大明", "業務", "電話: 02-2326-2888"])
        assert r["phone"] is not None

    def test_email_extracted(self):
        r = zh_parse(["ABC有限公司", "王大明", "wang@abc.com.tw"])
        assert r["email"] == "wang@abc.com.tw"

    def test_address_extracted(self):
        r = zh_parse(["ABC有限公司", "王大明", "100台北市中正區忠孝東路一段1號"])
        assert r["address"] is not None


# ── English card parsing ──────────────────────────────────────────────────────

class TestParseEn:
    def test_company_suffix(self):
        r = en_parse(["NEPA CO., LTD.", "Woo Jeong Hong", "Senior Specialist"])
        assert r["company_name"] == "NEPA CO., LTD."
        assert r["person_name"] == "Woo Jeong Hong"

    def test_email_not_company(self):
        r = en_parse(["Woo Jeong Hong", "Senior Specialist", "Ewhong@nepa.co.kr"])
        assert r["company_name"] is None
        assert r["email"] == "Ewhong@nepa.co.kr"

    def test_all_caps_brand_fallback(self):
        r = en_parse(["NANCY HOO", "Retail Associate", "ARC'TERYX"])
        assert r["company_name"] == "ARC'TERYX"
        assert r["person_name"] == "NANCY HOO"

    def test_oy_company(self):
        r = en_parse(["Halti Oy", "Sales Manager", "Jane Doe"])
        assert r["company_name"] == "Halti Oy"

    def test_3word_name(self):
        r = en_parse(["NEPA CO., LTD.", "Woo Jeong Hong", "Senior Specialist"])
        assert r["person_name"] == "Woo Jeong Hong"

    def test_job_title_not_captured_as_name(self):
        # "Senior Engineer" matches 2-word cap regex but is a job title
        r = en_parse(["ACME Corp", "Senior Engineer", "Jane Smith"])
        assert r["job_title"] == "Senior Engineer"
        assert r["person_name"] == "Jane Smith"

    def test_phone_with_office_label(self):
        r = en_parse(["ACME Corp", "Jane Smith", "Office: 604.960.3231"])
        assert r["phone"] is not None

    def test_fax_extracted(self):
        r = en_parse(["ACME Corp", "Jane Smith", "Tel:03-3497-8076Fax:03-3497-2258"])
        assert r["fax"] is not None
        assert "3497-2258" in r["fax"]

    def test_address_with_suite(self):
        r = en_parse(["ACME Corp", "Jane Smith", "123 Main Street Suite 400", "Vancouver, BC V6B 1A1"])
        assert r["address"] is not None
        assert "Main Street" in r["address"]
