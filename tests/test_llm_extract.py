"""Unit tests for the LLM extraction layer — pure functions + regex-fallback path.

No Ollama server or model is needed: build_prompt/compact_boxes are pure, the
CardExtraction contract is checked from canned JSON, and extract_card is exercised
only on its CARD_EXTRACTOR=regex path (which never touches Ollama).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas.extraction import CardExtraction
from app.services import card_extractor
from app.services.llm_parser import build_prompt, build_repair_prompt, compact_boxes

_TEN_KEYS = (
    "company_name", "person_name", "english_name", "job_title", "email",
    "phone", "mobile", "fax", "address", "website",
)


class TestCompactBoxes:
    def test_shape_and_skips_empty(self):
        boxes = [
            {"text": "ABC", "bbox": [[10, 20], [110, 20], [110, 60], [10, 60]], "confidence": 1.0},
            {"text": "  ", "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]]},   # 空白 → 跳過
            {"text": "John", "bbox": "bad"},                            # bbox 壞 → x=0, y=index
        ]
        out = compact_boxes(boxes)
        assert out[0] == {"i": 0, "x": 10, "y": 20, "text": "ABC"}
        assert [o["text"] for o in out] == ["ABC", "John"]

    def test_empty_input(self):
        assert compact_boxes([]) == []
        assert compact_boxes(None) == []


class TestBuildPrompt:
    def test_includes_text_candidates_lang(self):
        p = build_prompt(
            "王小明 david@abc.com", "zh",
            {"emails": ["david@abc.com"], "phones": ["02-2712-1234"], "websites": []},
            [{"i": 0, "x": 0, "y": 0, "text": "王小明"}],
        )
        assert "王小明" in p
        assert "david@abc.com" in p
        assert "02-2712-1234" in p
        assert "zh" in p

    def test_repair_prompt_lists_errors(self):
        p = build_repair_prompt({"email": "x"}, [{"field": "email", "message": "bad email"}], "raw", {})
        assert "email" in p and "bad email" in p


class TestCardExtractionContract:
    def test_canned_json_yields_ten_keys(self):
        ex = CardExtraction.model_validate_json(
            '{"company_name":"ABC","person_name":"王小明","email":"a@b.com"}'
        )
        d = ex.model_dump()
        for k in _TEN_KEYS:
            assert k in d
        assert d["company_name"] == "ABC"
        assert d["job_title"] is None   # 缺的欄位 → None


class TestExtractCardRegexFallback:
    def test_regex_mode_needs_no_ollama(self):
        os.environ["CARD_EXTRACTOR"] = "regex"
        try:
            boxes = [{"text": "王小明", "bbox": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 1.0}]
            out = asyncio.run(
                card_extractor.extract_card("王小明\ndavid@abc.com", boxes, "zh")
            )
            for k in _TEN_KEYS:
                assert k in out
            assert out["email"] == "david@abc.com"
        finally:
            os.environ.pop("CARD_EXTRACTOR", None)
