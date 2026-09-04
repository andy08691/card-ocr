"""
tests/test_cloud_image_prep.py — 雲端管線的影像前處理（純函式，離線）

不需要 API key、不需要網路、不需要 MinerU/Ollama。只需要 Pillow。
"""

import io

import pytest
from PIL import Image

from cloud.services import image_prep as ip


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestAlphaFlattening:
    """RGBA.convert('RGB') 只丟掉 alpha 通道，透明區底下通常是黑的 → 名片會變黑塊。

    注意：repo 的 test/test_card*.png 雖是 RGBA，但 alpha 全不透明，
    用天真的 convert('RGB') 也會通過——所以這裡必須自己合成一張真正透明的圖。
    """

    def test_transparent_png_flattens_to_white(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))          # 全透明
        out = ip.load_image(_png_bytes(img))
        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (255, 255, 255)

    def test_opaque_content_survives_flattening(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        img.paste((255, 0, 0, 255), (10, 10, 40, 40))            # 一塊不透明紅
        out = ip.load_image(_png_bytes(img))
        assert out.getpixel((20, 20)) == (255, 0, 0)
        assert out.getpixel((0, 0)) == (255, 255, 255)

    def test_palette_with_transparency(self):
        img = Image.new("P", (32, 32))
        img.info["transparency"] = 0
        out = ip.load_image(_png_bytes(img))
        assert out.mode == "RGB"

    def test_grayscale_converts_to_rgb(self):
        out = ip.load_image(_png_bytes(Image.new("L", (32, 32), 128)))
        assert out.mode == "RGB"


class TestExifOrientation:
    """手機橫拍的照片實際 pixel 是躺著的，靠 EXIF Orientation tag 轉正。"""

    def test_orientation_6_swaps_dimensions(self):
        img = Image.new("RGB", (100, 50), "white")
        exif = Image.Exif()
        exif[274] = 6                                            # 274 = Orientation
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        out = ip.load_image(buf.getvalue())
        assert out.size == (50, 100), "EXIF orientation 6 應該把寬高互換"

    def test_no_exif_leaves_dimensions_alone(self):
        img = Image.new("RGB", (100, 50), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        assert ip.load_image(buf.getvalue()).size == (100, 50)


class TestDownscale:
    def test_long_edge_capped(self):
        out = ip.downscale(Image.new("RGB", (4032, 3024)), 2048)
        assert max(out.size) == 2048
        assert abs(out.size[1] - int(3024 * 2048 / 4032)) <= 1   # 比例保持

    def test_portrait_long_edge_capped(self):
        out = ip.downscale(Image.new("RGB", (3024, 4032)), 2048)
        assert max(out.size) == 2048
        assert out.size[0] < out.size[1]

    def test_small_image_is_not_upscaled(self):
        out = ip.downscale(Image.new("RGB", (715, 441)), 2048)
        assert out.size == (715, 441), "只縮不放——放大沒有資訊增益，只是多付 token"

    def test_exactly_at_cap_untouched(self):
        out = ip.downscale(Image.new("RGB", (2048, 1000)), 2048)
        assert out.size == (2048, 1000)


class TestJpegLadder:
    def test_normal_budget_uses_requested_quality(self):
        _, q = ip.encode_jpeg(Image.new("RGB", (200, 200), "white"), 88, 4 * 1024 * 1024)
        assert q == 88

    def test_tiny_budget_steps_quality_down(self):
        img = Image.new("RGB", (900, 900))
        for x in range(0, 900, 3):                               # 高頻雜訊，壓不小
            for y in range(0, 900, 3):
                img.putpixel((x, y), ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256))
        data, q = ip.encode_jpeg(img, 88, 4096)
        assert q < 88, "超出位元組預算時 quality 必須降階"
        assert data.startswith(b"\xff\xd8\xff")


class TestDataUrlAndTokens:
    def test_data_url_round_trips(self):
        import base64

        jpeg, _ = ip.encode_jpeg(Image.new("RGB", (32, 32), "white"), 88, 10**7)
        url = ip.to_data_url(jpeg)
        assert url.startswith("data:image/jpeg;base64,")
        assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\xff\xd8\xff")

    @pytest.mark.parametrize("w,h,expected", [
        (715, 441, 386),        # test/test_card.png，實測值
        (1704, 961, 2008),      # test/275709_0.jpg，實測值
        (2048, 1536, 3686),     # 手機 4:3 縮圖後的典型尺寸
    ])
    def test_token_estimate(self, w, h, expected):
        assert ip.estimate_image_tokens(w, h) == expected


class TestPrepareImage:
    def test_end_to_end_shape(self):
        img = Image.new("RGB", (4032, 3024), "white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        p = ip.prepare_image(buf.getvalue(), max_edge=2048, quality=88, max_bytes=4 * 1024 * 1024)
        assert (p.width, p.height) == (2048, 1536)
        assert p.data_url.startswith("data:image/jpeg;base64,")
        assert p.quality == 88
        assert p.jpeg_bytes > 0

    def test_undecodable_bytes_raise(self):
        with pytest.raises(Exception):
            ip.prepare_image(b"definitely not an image", max_edge=2048, quality=88, max_bytes=10**7)


class TestHeic:
    def test_heic_opener_registers_when_available(self):
        pytest.importorskip("pillow_heif")
        assert ip.HEIC_SUPPORTED is True

    def test_flag_is_boolean_either_way(self):
        assert isinstance(ip.HEIC_SUPPORTED, bool)
