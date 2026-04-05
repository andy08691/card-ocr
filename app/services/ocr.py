import re
from typing import Optional
from paddleocr import PaddleOCR
import opencc

_converter = opencc.OpenCC("s2twp")  # Simplified → Traditional Taiwan

# Singleton instances — initialized lazily
_ocr_zh: Optional[PaddleOCR] = None
_ocr_en: Optional[PaddleOCR] = None


def _get_ocr(lang: str) -> PaddleOCR:
    global _ocr_zh, _ocr_en
    if lang == "en":
        if _ocr_en is None:
            _ocr_en = PaddleOCR(use_angle_cls=True, lang="en")
        return _ocr_en
    else:
        if _ocr_zh is None:
            _ocr_zh = PaddleOCR(use_angle_cls=True, lang="ch")
        return _ocr_zh


def _detect_language(text: str) -> str:
    """Return 'zh' if Chinese characters exceed 20% of total chars, else 'en'."""
    if not text:
        return "en"
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ratio = chinese_chars / len(text)
    return "zh" if ratio > 0.2 else "en"


def run_ocr(image_path: str) -> dict:
    """
    Run OCR on the given image path.
    Returns a dict with:
      - raw_text: str
      - ocr_confidence: float
      - boxes: list of (text, bbox, confidence)
      - lang: detected language
    """
    # First pass with Chinese model (handles mixed zh/en well)
    ocr = _get_ocr("zh")
    result = ocr.ocr(image_path, cls=True)

    if not result or not result[0]:
        return {"raw_text": "", "ocr_confidence": 0.0, "boxes": [], "lang": "en"}

    lines = result[0]
    boxes = []
    confidences = []

    for line in lines:
        bbox, (text, conf) = line
        text = _converter.convert(text)  # Simplified → Traditional
        boxes.append({"text": text, "bbox": bbox, "confidence": conf})
        confidences.append(conf)

    raw_text = "\n".join(b["text"] for b in boxes)
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    lang = _detect_language(raw_text)

    # Re-run with English model if pure English card for better accuracy
    if lang == "en":
        ocr_en = _get_ocr("en")
        result_en = ocr_en.ocr(image_path, cls=True)
        if result_en and result_en[0]:
            lines_en = result_en[0]
            boxes_en = []
            confidences_en = []
            for line in lines_en:
                bbox, (text, conf) = line
                boxes_en.append({"text": text, "bbox": bbox, "confidence": conf})
                confidences_en.append(conf)
            avg_en = sum(confidences_en) / len(confidences_en) if confidences_en else 0.0
            # Use English result if it has higher confidence
            if avg_en >= avg_confidence:
                boxes = boxes_en
                confidences = confidences_en
                raw_text = "\n".join(b["text"] for b in boxes)
                avg_confidence = avg_en

    return {
        "raw_text": raw_text,
        "ocr_confidence": round(avg_confidence, 4),
        "boxes": boxes,
        "lang": lang,
    }
