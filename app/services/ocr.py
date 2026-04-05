import re
from typing import Optional
from paddleocr import PaddleOCR
import paddleocr as _paddleocr_mod
import opencc

_converter = opencc.OpenCC("s2twp")  # Simplified → Traditional Taiwan

# Detect installed PaddleOCR major version to select the correct API
_IS_V3 = int(_paddleocr_mod.__version__.split(".")[0]) >= 3

# Singleton instances — initialized lazily
_ocr_zh: Optional[PaddleOCR] = None
_ocr_en: Optional[PaddleOCR] = None


def _get_ocr(lang: str) -> PaddleOCR:
    global _ocr_zh, _ocr_en
    if lang == "en":
        if _ocr_en is None:
            if _IS_V3:
                _ocr_en = PaddleOCR(lang="en")
            else:
                _ocr_en = PaddleOCR(use_angle_cls=True, lang="en")
        return _ocr_en
    else:
        if _ocr_zh is None:
            if _IS_V3:
                _ocr_zh = PaddleOCR(lang="ch")
            else:
                _ocr_zh = PaddleOCR(use_angle_cls=True, lang="ch")
        return _ocr_zh


def _extract_boxes(ocr: PaddleOCR, image_path: str) -> list:
    """Run OCR and return a list of {text, bbox, confidence} dicts."""
    boxes = []
    if _IS_V3:
        result = ocr.predict(image_path)
        if not result or not result[0]["res"]["rec_texts"]:
            return boxes
        for res in result:
            for text, conf, bbox in zip(
                res["res"]["rec_texts"],
                res["res"]["rec_scores"],
                res["res"]["dt_polys"],
            ):
                boxes.append({"text": text, "bbox": bbox, "confidence": conf})
    else:
        result = ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            return boxes
        for line in result[0]:
            bbox, (text, conf) = line
            boxes.append({"text": text, "bbox": bbox, "confidence": conf})
    return boxes


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
    boxes = _extract_boxes(ocr, image_path)

    if not boxes:
        return {"raw_text": "", "ocr_confidence": 0.0, "boxes": [], "lang": "en"}

    for b in boxes:
        b["text"] = _converter.convert(b["text"])  # Simplified → Traditional

    raw_text = "\n".join(b["text"] for b in boxes)
    confidences = [b["confidence"] for b in boxes]
    avg_confidence = sum(confidences) / len(confidences)
    lang = _detect_language(raw_text)

    # Re-run with English model if pure English card for better accuracy
    if lang == "en":
        ocr_en = _get_ocr("en")
        boxes_en = _extract_boxes(ocr_en, image_path)
        if boxes_en:
            confidences_en = [b["confidence"] for b in boxes_en]
            avg_en = sum(confidences_en) / len(confidences_en)
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
