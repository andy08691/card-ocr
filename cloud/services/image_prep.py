"""
cloud/services/image_prep.py — 送雲端 VLM 前的影像正規化（純函式、不動原檔）

與 app/services/ocr.py:93 的 _resize_if_needed() 刻意相反：那支做 img.save(image_path)，
是「原地覆蓋」，會把使用者上傳的高解析原圖直接改小、原始畫質永久消失。
這裡的函式只吃 bytes、吐 bytes，永遠不碰路徑——media/ 內的原檔保持原樣，
縮圖只活在記憶體裡、只為了送 API。

處理順序（每一步都有實務原因，順序不可換）：
  1. EXIF transpose  — 手機照片幾乎都帶 Orientation tag，不轉正模型會看到躺著的名片。
                       必須在縮圖之前：轉正會消耗 tag 並回傳新影像，順序倒過來會拿錯長短邊。
  2. 白底去 alpha    — JPEG 不支援 alpha。注意 RGBA.convert("RGB") 只是把 alpha 通道丟掉，
                       透明區底下的 RGB 通常是黑的 → 透明背景的名片會變成一塊黑色，
                       所以要用白底合成（名片實體背景本來就是白的）。
  3. 長邊縮圖        — 名片文字用不到 12MP。只縮不放（放大沒有資訊增益、只是多付 token）。
  4. JPEG 品質階梯   — q88 起，超過位元組預算就降階，避免 data URL 過胖。
  5. base64 data URL — Responses API 的 input_image 格式。

HEIC：pillow-heif 是選配。裝了就註冊 opener，Image.open() 直接吃 HEIC，後面流程完全不變；
沒裝就把 HEIC_SUPPORTED 設 False，讓 router 不要對外宣告收得下 heic
（宣告一個解不了的 MIME 會把乾淨的 400 變成 500）。
"""

import base64
import io
import logging
import math
from typing import Optional

from PIL import Image, ImageOps
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── HEIC 支援（選配）──────────────────────────────────────────────────────────
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:  # 未安裝就當作不支援，不讓整個模組炸掉
    HEIC_SUPPORTED = False

# ── JPEG 品質階梯 ─────────────────────────────────────────────────────────────
# 第一階由 CLOUD_IMAGE_JPEG_QUALITY 決定（預設 88），後面是超出位元組預算時的降階順序。
_QUALITY_STEPS = (78, 68, 58)

# 模型把影像切成 32×32 patch，再乘上模型係數（gpt-5.6-luna ≈ 1.2）。
_PATCH = 32
_PATCH_TOKEN_FACTOR = 1.2

# 存進 media/ 的 HEIC 轉檔品質。瀏覽器不渲染 HEIC，所以原圖存成 JPEG 衍生檔，
# 但品質拉高到 95 讓它仍是「全解析度存檔」而不是壓過的縮圖。
HEIC_ARCHIVE_QUALITY = 95


class PreparedImage(BaseModel):
    """prepare_image() 的輸出。data_url 直接餵給 Responses API 的 input_image。"""

    data_url: str
    width: int
    height: int
    jpeg_bytes: int
    quality: int
    source_format: Optional[str] = None   # "PNG" / "JPEG" / "HEIF"…（僅供 log）


def load_image(data: bytes) -> Image.Image:
    """開檔 → EXIF 轉正 → 壓成 RGB（透明區填白）。丟不進來的位元組會讓 PIL 拋例外。"""
    img = Image.open(io.BytesIO(data))
    img.load()

    # 1. EXIF 轉正——必須在任何 resize 之前
    img = ImageOps.exif_transpose(img)

    # 2. 去 alpha：用白底合成，不能只 convert("RGB")
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    return img


def downscale(img: Image.Image, max_edge: int) -> Image.Image:
    """長邊超過 max_edge 才縮，只縮不放（條件與 _resize_if_needed 一致）。"""
    w, h = img.size
    if max(w, h) <= max_edge:
        return img
    scale = max_edge / max(w, h)
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def encode_jpeg(img: Image.Image, quality: int, max_bytes: int) -> tuple:
    """編成 JPEG；超過 max_bytes 就沿品質階梯降階。回傳 (jpeg_bytes, 實際使用的 quality)。"""
    for q in (quality, *[s for s in _QUALITY_STEPS if s < quality]):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data, q
    # 階梯走完仍超標：回傳最後一階（護欄是位元組預算，不是硬性失敗——
    # 上游已用 CLOUD_MAX_UPLOAD_BYTES 擋過真正誇張的檔案）
    return data, q


def to_data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def estimate_image_tokens(width: int, height: int) -> int:
    """估算 image tokens：ceil(w/32) * ceil(h/32) * 1.2。純粹用來記帳與 dry-run。"""
    patches = math.ceil(width / _PATCH) * math.ceil(height / _PATCH)
    return int(patches * _PATCH_TOKEN_FACTOR)


def prepare_image(data: bytes, *, max_edge: int, quality: int, max_bytes: int) -> PreparedImage:
    """對外唯一入口：上傳的原始位元組 → 可直接送 API 的 PreparedImage。"""
    img = load_image(data)
    source_format = img.format or Image.open(io.BytesIO(data)).format
    original = img.size

    img = downscale(img, max_edge)
    jpeg, used_quality = encode_jpeg(img, quality, max_bytes)

    logger.info(
        "Image prepared: %dx%d -> %dx%d q=%d bytes=%d est_tokens=%d src=%s",
        original[0], original[1], img.size[0], img.size[1],
        used_quality, len(jpeg), estimate_image_tokens(*img.size), source_format,
    )
    return PreparedImage(
        data_url=to_data_url(jpeg),
        width=img.size[0],
        height=img.size[1],
        jpeg_bytes=len(jpeg),
        quality=used_quality,
        source_format=source_format,
    )


def to_archive_jpeg(data: bytes) -> bytes:
    """把 HEIC 轉成全解析度 JPEG，供存檔用（不縮圖）。

    為什麼 HEIC 不原樣存：CardResponse.image_path 會透過 /media 給瀏覽器讀，
    而主流瀏覽器都不渲染 HEIC，前端的欄位校正 UI 會顯示破圖。
    其他格式一律原樣寫入，只有 HEIC 走這個例外。
    """
    img = load_image(data)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=HEIC_ARCHIVE_QUALITY, optimize=True)
    return buf.getvalue()
