"""
services/ocr.py — PaddleOCR 封裝

主要功能：
  run_ocr(image_path) → dict
    - 自動縮圖（超過 3000px 的圖片縮小後再 OCR）
    - 以中文模型做第一次辨識（中文模型對中英混排效果好）
    - 偵測語言後，若為純英文名片，改用英文模型再跑一次並比較信心分數
    - 簡體字轉繁體（OpenCC s2twp 模式）
    - 回傳 raw_text、ocr_confidence、boxes、lang

版本兼容：
  同一份 ocr.py 同時支援 PaddleOCR 2.x（macOS）與 3.x（Windows）。
  在模組載入時偵測版本，對應不同的 API 呼叫方式與結果格式。

  PaddleOCR 2.x：
    建構子：PaddleOCR(use_angle_cls=True, lang="ch")
    呼叫：  ocr.ocr(image_path, cls=True)
    結果：  result[0] = [(bbox, (text, conf)), ...]

  PaddleOCR 3.x：
    建構子：PaddleOCR(lang="ch")
    呼叫：  ocr.predict(image_path)
    結果：  result[i]["res"] = {"rec_texts": [...], "rec_scores": [...], "dt_polys": [...]}
"""

import logging
import re
import time
from typing import Optional
from PIL import Image
from paddleocr import PaddleOCR
import paddleocr as _paddleocr_mod
import opencc

logger = logging.getLogger(__name__)

# ── 版本偵測 ──────────────────────────────────────────────────────────────────
# 在模組載入時偵測 PaddleOCR 主版本號，決定使用 2.x 還是 3.x API 路徑
_IS_V3 = int(_paddleocr_mod.__version__.split(".")[0]) >= 3

# OpenCC 轉換器：簡體 → 繁體台灣（s2twp 模式包含詞彙修正）
_converter = opencc.OpenCC("s2twp")

# ── 圖片縮圖設定 ──────────────────────────────────────────────────────────────
# 超過此像素數（長邊）的圖片會在 OCR 前先縮小，加快處理速度且不影響精度
_MAX_OCR_PIXELS = 3000

# ── OCR 實例快取（Singleton，懶初始化）────────────────────────────────────────
# PaddleOCR 初始化時會載入約 50–100MB 的模型，因此只建立一次並重複使用
# _ocr_zh：處理中文（含中英混排）
# _ocr_en：處理純英文名片（字體辨識更準確）
_ocr_zh: Optional[PaddleOCR] = None
_ocr_en: Optional[PaddleOCR] = None


def _get_ocr(lang: str) -> PaddleOCR:
    """取得對應語言的 PaddleOCR singleton 實例（首次呼叫時初始化）。

    Args:
        lang: "zh"（中文模型）或 "en"（英文模型）
    """
    global _ocr_zh, _ocr_en
    if lang == "en":
        if _ocr_en is None:
            if _IS_V3:
                _ocr_en = PaddleOCR(lang="en")
            else:
                # use_angle_cls=True：啟用文字方向分類器，支援旋轉名片
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
    """執行 OCR 並回傳統一格式的 box 清單。

    回傳格式：[{"text": str, "bbox": list, "confidence": float}, ...]

    PaddleOCR 2.x 與 3.x 結果格式不同，此函式統一封裝：
      - bbox：文字區塊的四個頂點座標（[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]）
      - confidence：辨識信心分數（0.0–1.0）
    """
    boxes = []
    if _IS_V3:
        # 3.x API：result 為 list，每個元素有 res 字典
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
        # 2.x API：result[0] 為 [(bbox, (text, conf)), ...] 格式
        result = ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            return boxes
        for line in result[0]:
            bbox, (text, conf) = line
            boxes.append({"text": text, "bbox": bbox, "confidence": conf})
    return boxes


def _detect_language(text: str) -> str:
    """偵測文字語言，判斷是中文名片還是英文名片。

    計算中文字（CJK）占全部字元的比例：
      > 20% → 中文名片（lang="zh"）
      ≤ 20% → 英文名片（lang="en"）

    混排名片（如日文名片含少量漢字）通常比例會超過 20%，
    因此會走中文解析路徑，這是預期行為。
    """
    if not text:
        return "en"
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ratio = chinese_chars / len(text)
    return "zh" if ratio > 0.2 else "en"


def _resize_if_needed(image_path: str) -> None:
    """若圖片長邊超過 _MAX_OCR_PIXELS，縮小後原地覆蓋。

    高解析度照片（手機拍攝的名片通常 > 4000px）不需要完整解析度做 OCR，
    縮圖後可大幅降低記憶體使用並加快識別速度，且不影響文字辨識精度。
    """
    img = Image.open(image_path)
    w, h = img.size
    if max(w, h) > _MAX_OCR_PIXELS:
        scale = _MAX_OCR_PIXELS / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img.save(image_path)
        logger.info("Resized image %s from %dx%d to %dx%d", image_path, w, h, new_w, new_h)


def run_ocr(image_path: str) -> dict:
    """對指定圖片執行 OCR，回傳結構化結果。

    流程：
      1. 縮圖（若需要）
      2. 中文模型第一次辨識
      3. 簡體 → 繁體轉換（OpenCC）
      4. 偵測語言
      5. 若為英文名片，用英文模型再跑一次，取信心分數較高者
         （英文模型信心分數允許比中文模型低 0.01 仍優先使用）

    Returns:
        {
            "raw_text": str,           # 所有 box 文字以換行連接
            "ocr_confidence": float,   # 平均信心分數（四捨五入至小數點後 4 位）
            "boxes": list,             # [{text, bbox, confidence}, ...]
            "lang": str,               # "zh" 或 "en"
        }
    """
    _resize_if_needed(image_path)

    t0 = time.monotonic()

    # ── 第一次：中文模型 ─────────────────────────────────────────────────────
    ocr = _get_ocr("zh")
    boxes = _extract_boxes(ocr, image_path)

    if not boxes:
        return {"raw_text": "", "ocr_confidence": 0.0, "boxes": [], "lang": "en"}

    # 簡體轉繁體（對英文文字透明，不影響輸出）
    for b in boxes:
        b["text"] = _converter.convert(b["text"])

    raw_text = "\n".join(b["text"] for b in boxes)
    confidences = [b["confidence"] for b in boxes]
    avg_confidence = sum(confidences) / len(confidences)
    lang = _detect_language(raw_text)

    # ── 第二次：英文模型（只在純英文名片時觸發）─────────────────────────────
    # 英文模型對拉丁字母字型更專精，給予 0.01 的信心分數加成
    # 確保在兩者差距極小時優先選用英文模型
    if lang == "en":
        ocr_en = _get_ocr("en")
        boxes_en = _extract_boxes(ocr_en, image_path)
        if boxes_en:
            confidences_en = [b["confidence"] for b in boxes_en]
            avg_en = sum(confidences_en) / len(confidences_en)
            if avg_en > avg_confidence - 0.01:
                boxes = boxes_en
                confidences = confidences_en
                raw_text = "\n".join(b["text"] for b in boxes)
                avg_confidence = avg_en

    elapsed = time.monotonic() - t0
    logger.info(
        "OCR done: lang=%s confidence=%.4f boxes=%d time=%.2fs path=%s",
        lang, avg_confidence, len(boxes), elapsed, image_path,
    )

    return {
        "raw_text": raw_text,
        "ocr_confidence": round(avg_confidence, 4),
        "boxes": boxes,
        "lang": lang,
    }
