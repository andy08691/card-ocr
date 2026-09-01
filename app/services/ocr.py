"""
services/ocr.py — MinerU 封裝（取代原本的 PaddleOCR）

主要功能：
  run_ocr(image_path) → dict
    - 自動縮圖（超過 3000px 的圖片縮小後再辨識）
    - 以 MinerU 執行文字擷取（名片圖片會被包成單頁 PDF 再解析）
    - 簡體字轉繁體（OpenCC s2twp 模式）
    - 偵測語言（中文 / 英文），回傳 raw_text、ocr_confidence、boxes、lang

為什麼用 MinerU：
  MinerU（OpenDataLab）的 VLM 模型對名片的文字擷取比 PaddleOCR 更精準。
  本模組只負責「圖片 → 文字」，下游的欄位解析（parser.py）維持不變，
  因此 run_ocr 的回傳結構刻意與舊版完全相同，作為 OCR ↔ parser 的契約。

後端與硬體：
  預設後端 "vlm-engine"，MinerU 會依硬體自動挑推論引擎：
    - macOS（安裝 mlx-vlm）→ MLX（Apple Silicon 加速）
    - Linux + vllm         → vllm（GPU）
    - 其餘                 → transformers（CPU fallback）
  可用環境變數 MINERU_BACKEND 覆寫（例如 pipeline / hybrid-engine）。

在 FastAPI 進程內執行：
  MinerU 內部使用 spawn 模式的 ProcessPoolExecutor 做 PDF 轉圖。
  以 uvicorn 啟動時 __main__ 為 uvicorn 入口，spawn 子行程會以 __mp_main__
  重新 import，不會重跑啟動程式碼，因此進程內呼叫 do_parse 是安全的。
  另以模組層級的鎖序列化辨識，避免併發時的模型競態並控制 16GB 機器的記憶體。
"""

import glob
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import Image
import opencc

logger = logging.getLogger(__name__)

# ── MinerU 執行環境調校 ────────────────────────────────────────────────────────
# 名片只有一頁，PDF 轉圖用單一 worker 即可：省記憶體、減少 spawn 子行程成本。
# 使用 setdefault，讓外部仍可用環境變數覆寫。
os.environ.setdefault("MINERU_PDF_RENDER_THREADS", "1")

# OpenCC 轉換器：簡體 → 繁體台灣（s2twp 模式包含詞彙修正）
_converter = opencc.OpenCC("s2twp")

# ── 圖片縮圖設定 ──────────────────────────────────────────────────────────────
# 超過此像素數（長邊）的圖片會在辨識前先縮小，降低記憶體使用
_MAX_OCR_PIXELS = 3000

# ── 辨識序列化鎖 ──────────────────────────────────────────────────────────────
# MinerU 以進程內 singleton 快取模型；用鎖確保同時只有一次辨識在跑，
# 避免併發請求造成模型競態，並在 16GB 機器上控制記憶體峰值。
_ocr_lock = threading.Lock()


def _select_backend() -> str:
    """選擇 MinerU 後端。

    預設 "vlm-engine"：MinerU 的 inference_engine='auto' 會依硬體自動挑加速器
    （Mac→MLX、Linux+CUDA→vllm、其餘→transformers）。可用 MINERU_BACKEND 覆寫。

    以 `or` 處理環境變數為空字串的情況（.env 常寫成 `MINERU_BACKEND=`），
    此時 os.getenv 會回傳 ""，需視為未設定並套用預設值。
    """
    return os.getenv("MINERU_BACKEND") or "vlm-engine"


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
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    ratio = chinese_chars / len(text)
    return "zh" if ratio > 0.2 else "en"


def _resize_if_needed(image_path: str) -> None:
    """若圖片長邊超過 _MAX_OCR_PIXELS，縮小後原地覆蓋。

    高解析度照片（手機拍攝的名片通常 > 4000px）不需要完整解析度做辨識，
    縮圖後可降低記憶體使用，且不影響文字辨識精度。
    """
    img = Image.open(image_path)
    w, h = img.size
    if max(w, h) > _MAX_OCR_PIXELS:
        scale = _MAX_OCR_PIXELS / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        img.save(image_path)
        logger.info("Resized image %s from %dx%d to %dx%d", image_path, w, h, new_w, new_h)


def _bbox_to_polygon(bbox) -> list:
    """把 MinerU 的 [x0, y0, x1, y1] 轉成 parser 需要的四點多邊形。

    parser._box_top() 以 min(pt[1] for pt in bbox) 取上緣做由上到下排序，
    因此只需提供含 [x, y] 點的可迭代結構即可（x 與 confidence 解析器不使用）。
    格式異常時回傳 [[0, 0]]，讓 _box_top 取到 0 並保留原輸入順序。
    """
    try:
        x0, y0, x1, y1 = bbox
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    except Exception:
        return [[0, 0]]


def _content_list_to_boxes(content_list: list) -> list:
    """把 MinerU 的 content_list.json 轉成 run_ocr 的 boxes 格式。

    - 只保留有 text 的區塊；image / table 等區塊沒有 text 欄位（logo 以
      content 呈現，常是雜訊），一律略過。
    - 每個 box 的文字做簡→繁轉換（對英文透明）。
    - confidence 固定為 1.0：MinerU 的 content_list 不提供 per-box 分數，
      而解析器本來就不使用此欄位。
    """
    boxes = []
    for item in content_list:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        text = _converter.convert(text)
        boxes.append({
            "text": text,
            "bbox": _bbox_to_polygon(item.get("bbox")),
            "confidence": 1.0,
        })
    return boxes


def _run_mineru(image_path: str) -> list:
    """對圖片執行 MinerU，回傳 content_list（區塊清單）。

    do_parse 會把結果寫到暫存目錄，再讀回 <name>_content_list.json。
    以 _ocr_lock 序列化，確保同時只有一次辨識在跑。
    """
    # lazy import：避免 web 進程啟動時就載入 torch / mlx 等重量級套件
    from mineru.cli.common import do_parse, read_fn

    img = Path(image_path)
    with _ocr_lock:
        with tempfile.TemporaryDirectory() as out_dir:
            do_parse(
                out_dir,
                [img.stem],
                [read_fn(img)],          # 圖片會被包成單頁 PDF
                ["ch"],                  # pipeline 後端的 OCR 語言；VLM 會忽略
                backend=_select_backend(),
                formula_enable=False,    # 名片沒有公式 / 表格，關閉以加速
                table_enable=False,
                image_analysis=False,    # 名片不需圖表/logo 分析，關閉省一次 VLM pass
            )
            matches = [
                m for m in glob.glob(os.path.join(out_dir, "**", "*_content_list.json"),
                                     recursive=True)
                if m.endswith("_content_list.json")   # 排除 *_content_list_v2.json
            ]
            if not matches:
                logger.warning("MinerU produced no content_list.json for %s", image_path)
                return []
            with open(matches[0], encoding="utf-8") as f:
                return json.load(f)


def run_ocr(image_path: str) -> dict:
    """對指定圖片執行 OCR，回傳結構化結果。

    流程：
      1. 縮圖（若需要）
      2. MinerU 文字擷取
      3. 簡體 → 繁體轉換（在 _content_list_to_boxes 內完成）
      4. 偵測語言

    Returns:
        {
            "raw_text": str,           # 所有 box 文字以換行連接（依 MinerU 閱讀順序）
            "ocr_confidence": float,   # MinerU 不提供分數，成功為 1.0、無結果為 0.0
            "boxes": list,             # [{text, bbox, confidence}, ...]
            "lang": str,               # "zh" 或 "en"
        }
    """
    _resize_if_needed(image_path)

    t0 = time.monotonic()
    content_list = _run_mineru(image_path)
    boxes = _content_list_to_boxes(content_list)

    if not boxes:
        return {"raw_text": "", "ocr_confidence": 0.0, "boxes": [], "lang": "en"}

    raw_text = "\n".join(b["text"] for b in boxes)
    lang = _detect_language(raw_text)

    logger.info(
        "MinerU OCR done: lang=%s boxes=%d time=%.2fs path=%s",
        lang, len(boxes), time.monotonic() - t0, image_path,
    )

    return {
        "raw_text": raw_text,
        "ocr_confidence": 1.0,
        "boxes": boxes,
        "lang": lang,
    }


def warmup() -> None:
    """預先載入 MinerU 模型，避免第一個請求付出載入成本。

    產生一張很小的合成圖片跑一次 run_ocr，把 ~數 GB 模型載入進程內快取。
    設計為非致命：任何失敗只記 log，不影響 server 啟動。
    """
    try:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "warmup.png")
            Image.new("RGB", (320, 160), "white").save(p)
            t0 = time.monotonic()
            _run_mineru(p)
            logger.info("MinerU warmup done in %.2fs (backend=%s)",
                        time.monotonic() - t0, _select_backend())
    except Exception:
        logger.exception("MinerU warmup failed (non-fatal); model will load on first request")
