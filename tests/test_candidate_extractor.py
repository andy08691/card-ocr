"""Unit tests for app/services/candidate_extractor.py — pure regex, no LLM/server."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.candidate_extractor import extract_candidates


def _digits(items):
    return [re.sub(r"\D", "", x) for x in items]


class TestEmails:
    def test_finds_all_emails(self):
        c = extract_candidates("聯絡 david.wang@abc.com.tw 或 sales@abc.com.tw")
        assert "david.wang@abc.com.tw" in c.emails
        assert "sales@abc.com.tw" in c.emails

    def test_dedup(self):
        c = extract_candidates("a@b.com a@b.com")
        assert c.emails == ["a@b.com"]


class TestWebsites:
    def test_finds_www_and_http(self):
        c = extract_candidates("www.abc.com.tw 及 https://foo.example.com")
        assert "www.abc.com.tw" in c.websites
        assert "https://foo.example.com" in c.websites

    def test_bare_email_domain_not_website(self):
        c = extract_candidates("david@abc.com.tw")
        assert c.websites == []


class TestPhones:
    def test_union_without_classification(self):
        text = "Tel:02-2712-1234\nMobile:0912-345-678\nFax:02-2712-9999"
        c = extract_candidates(text)
        d = _digits(c.phones)
        assert "0227121234" in d
        assert "0912345678" in d
        assert "0227129999" in d

    def test_dedup_by_digits(self):
        c = extract_candidates("02-2712-1234\n(02) 2712 1234")
        assert _digits(c.phones).count("0227121234") == 1

    def test_intl_format(self):
        c = extract_candidates("+886 2 2712 1234")
        assert any("886" in re.sub(r"\D", "", p) for p in c.phones)


class TestEmpty:
    def test_no_contacts(self):
        c = extract_candidates("王小明 業務經理 台北市")
        assert c.emails == [] and c.websites == [] and c.phones == []
