"""Unit tests for the MinerU→boxes adapter in app/services/ocr.py.

These test only the pure adapter functions (no MinerU model load, no server):
  - _bbox_to_polygon:    MinerU [x0,y0,x1,y1] → parser 的四點多邊形
  - _content_list_to_boxes: content_list.json → run_ocr 的 boxes 格式
  - _detect_language:    中文 / 英文判斷
And an end-to-end check that adapter-produced boxes are accepted by parse_card.

Importing app.services.ocr is cheap: torch/mlx/mineru are lazy-imported inside
_run_mineru, so collecting these tests does not load any heavy ML dependency.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ocr import (
    _bbox_to_polygon,
    _content_list_to_boxes,
    _detect_language,
)
from app.services.parser import _box_top, parse_card


class TestBboxToPolygon:
    def test_flat_to_4point(self):
        assert _bbox_to_polygon([10, 20, 110, 60]) == [
            [10, 20], [110, 20], [110, 60], [10, 60]
        ]

    def test_top_edge_via_box_top(self):
        # parser._box_top 取 min y，應等於 y0
        assert _box_top(_bbox_to_polygon([10, 20, 110, 60])) == 20

    def test_malformed_bbox_falls_back(self):
        # 格式異常回傳 [[0,0]]，_box_top 取 0（保留原輸入順序）
        assert _box_top(_bbox_to_polygon(None)) == 0
        assert _box_top(_bbox_to_polygon([1, 2, 3])) == 0


class TestContentListToBoxes:
    def test_skips_blocks_without_text(self):
        cl = [
            {"type": "text", "text": "Hello", "bbox": [0, 0, 10, 10]},
            {"type": "image", "content": "LOGO", "bbox": [0, 0, 10, 10]},  # 無 text → 跳過
            {"type": "text", "text": "   ", "bbox": [0, 0, 10, 10]},        # 空白 → 跳過
            {"type": "text", "bbox": [0, 0, 10, 10]},                        # 缺 text key → 跳過
        ]
        boxes = _content_list_to_boxes(cl)
        assert [b["text"] for b in boxes] == ["Hello"]

    def test_simplified_to_traditional(self):
        boxes = _content_list_to_boxes([
            {"type": "text", "text": "销售顾问", "bbox": [0, 0, 10, 10]},
        ])
        assert boxes[0]["text"] == "銷售顧問"

    def test_confidence_is_constant_one(self):
        boxes = _content_list_to_boxes([
            {"type": "text", "text": "x", "bbox": [0, 0, 1, 1]},
        ])
        assert boxes[0]["confidence"] == 1.0

    def test_bbox_converted_to_4point(self):
        boxes = _content_list_to_boxes([
            {"type": "text", "text": "x", "bbox": [1, 2, 3, 4]},
        ])
        assert boxes[0]["bbox"] == [[1, 2], [3, 2], [3, 4], [1, 4]]

    def test_boxes_sortable_top_to_bottom(self):
        cl = [
            {"type": "text", "text": "top", "bbox": [0, 10, 100, 30]},
            {"type": "text", "text": "bottom", "bbox": [0, 200, 100, 230]},
        ]
        boxes = _content_list_to_boxes(cl)
        ordered = sorted(boxes, key=lambda b: _box_top(b["bbox"]))
        assert [b["text"] for b in ordered] == ["top", "bottom"]

    def test_empty_content_list(self):
        assert _content_list_to_boxes([]) == []


class TestDetectLanguage:
    def test_zh(self):
        assert _detect_language("台灣積體電路製造股份有限公司") == "zh"

    def test_en(self):
        assert _detect_language("John Smith Marketing Director") == "en"

    def test_empty_defaults_en(self):
        assert _detect_language("") == "en"


class TestAdapterFeedsParser:
    """adapter 產出的 boxes 應可直接被 parse_card 使用（契約相容）。"""

    def test_end_to_end_email_and_keys(self):
        cl = [
            {"type": "text", "text": "台積電股份有限公司", "bbox": [0, 10, 200, 40]},
            {"type": "text", "text": "王小明", "bbox": [0, 50, 120, 80]},
            {"type": "text", "text": "E-mail: ming@tsmc.com", "bbox": [0, 90, 260, 120]},
        ]
        boxes = _content_list_to_boxes(cl)
        raw_text = "\n".join(b["text"] for b in boxes)
        parsed = parse_card(raw_text=raw_text, boxes=boxes, lang=_detect_language(raw_text))
        # email 由 raw_text regex 擷取，最穩定
        assert parsed["email"] == "ming@tsmc.com"
        # 回傳應包含所有結構化欄位鍵
        for key in ("company_name", "person_name", "english_name", "job_title",
                    "email", "phone", "mobile", "fax", "address", "website"):
            assert key in parsed
