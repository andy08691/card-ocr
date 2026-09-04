"""
tests/test_cloud_pipeline.py — 雲端 workflow（離線，零花費）

OpenAI client 用**依賴注入**取代，不是 monkeypatch：run_cloud_pipeline 接受
extractor 參數，測試塞一個鴨子型別進去就能驗證整條 workflow，完全不發網路請求。
"""

import asyncio
import os
import subprocess
import sys
import textwrap

import pytest

from app.schemas.extraction import CardExtraction
from cloud.schemas.vision import CardVisionResult
from cloud.services import card_pipeline
from cloud.services.image_prep import PreparedImage

_TEN_KEYS = {
    "company_name", "person_name", "english_name", "job_title", "email",
    "phone", "mobile", "fax", "address", "website",
}

_PREPARED = PreparedImage(
    data_url="data:image/jpeg;base64,AAA", width=2048, height=1536,
    jpeg_bytes=1234, quality=88, source_format="JPEG",
)


def _result(raw_text, **fields) -> CardVisionResult:
    return CardVisionResult(raw_text=raw_text, fields=CardExtraction(**fields))


class _FakeVision:
    """鴨子型別冒充 VisionCardExtractor；依序吐出預先準備好的 CardVisionResult。"""

    def __init__(self, *results):
        self._results = list(results)
        self.extract_calls = 0
        self.repair_calls = 0
        self.last_usage = {"input_tokens": 100, "output_tokens": 50,
                           "cached_tokens": 0, "reasoning_tokens": 20}

    def _pop(self):
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]

    async def extract(self, prepared, cache_key=None):
        self.extract_calls += 1
        return self._pop()

    async def repair(self, prepared, previous, issues, raw_text, candidates, cache_key=None):
        self.repair_calls += 1
        return self._pop()


def _run(fake, **env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        return asyncio.run(card_pipeline.run_cloud_pipeline(_PREPARED, extractor=fake))
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class TestHappyPath:
    def test_returns_exactly_ten_fields(self):
        fake = _FakeVision(_result(
            "高都汽車股份有限公司\n陳志均\nTel: 04-2326-2888\nAFDA914@toyota.com.tw",
            company_name="高都汽車股份有限公司", person_name="陳志均",
            phone="04-2326-2888", email="AFDA914@toyota.com.tw",
        ))
        out = _run(fake)
        assert set(out.fields) == _TEN_KEYS
        assert fake.repair_calls == 0
        assert out.issues == []

    def test_ocr_confidence_matches_gpu_pipeline(self):
        # app/ 在 MinerU 下恆為 1.0；比照填 1.0 讓兩條管線的資料列無從分辨
        out = _run(_FakeVision(_result("x", person_name="A")))
        assert out.ocr_confidence == 1.0

    def test_usage_is_accumulated_for_cost_accounting(self):
        out = _run(_FakeVision(_result("x", person_name="A")))
        assert out.usage["calls"] == 1
        assert out.usage["input_tokens"] == 100
        assert out.usage["repairs"] == 0


class TestFormatDriftSnapping:
    """模型重新格式化電話 → _snap_candidates 還原成卡片上印的表面字串。

    這是這層把關最主要的價值：讓 DB 與 GPU 管線對同一張卡的輸出保持一致。
    """

    def test_phone_snaps_back_to_printed_surface_string(self):
        fake = _FakeVision(_result(
            "高都汽車\n電話（04）2326-2888",
            company_name="高都汽車", phone="04-2326-2888",   # 模型改寫了分隔符
        ))
        out = _run(fake)
        assert out.fields["phone"] == "（04）2326-2888", "應該 snap 回全形括號的原樣"
        assert fake.repair_calls == 0, "數字相同 → 驗證通過，不該白花一次 repair"


class TestRepairLoop:
    def test_single_repair_fixes_the_issue(self):
        bad = _result("Tel: 04-2326-2888\nian@sprayway.com",
                      email="typo@sprayway.com", phone="04-2326-2888")
        good = _result("Tel: 04-2326-2888\nian@sprayway.com",
                       email="ian@sprayway.com", phone="04-2326-2888")
        fake = _FakeVision(bad, good)
        out = _run(fake, CLOUD_MAX_REPAIR=1)
        assert fake.repair_calls == 1
        assert out.issues == []
        assert out.fields["email"] == "ian@sprayway.com"
        assert out.usage["repairs"] == 1

    def test_repair_rewriting_raw_text_recomputes_candidates(self):
        """repair 可能改寫 raw_text——候選必須重算，否則等於拿舊尺量新布。

        第一次轉錄漏抄了 email；repair 補上轉錄並填欄位。
        若候選沿用舊的 raw_text（沒有那個 email），新的正確值會被誤判為 issue。
        """
        first = _result("Tel: 04-2326-2888", email="ian@sprayway.com", phone="04-2326-2888")
        second = _result("Tel: 04-2326-2888\nEmail: ian@sprayway.com",
                         email="ian@sprayway.com", phone="04-2326-2888")
        fake = _FakeVision(first, second)
        out = _run(fake, CLOUD_MAX_REPAIR=1)
        assert fake.repair_calls == 1
        assert out.issues == [], "候選重算後，補上的 email 應該通過驗證"
        assert out.fields["email"] == "ian@sprayway.com"

    def test_best_effort_when_repair_does_not_help(self):
        bad = _result("Tel: 04-2326-2888\nian@sprayway.com", email="ghost@nowhere.com")
        fake = _FakeVision(bad, bad)
        out = _run(fake, CLOUD_MAX_REPAIR=1)
        assert fake.repair_calls == 1
        assert [i["field"] for i in out.issues] == ["email"]
        assert out.fields["email"] == "ghost@nowhere.com", "存 best-effort，不整張失敗"

    def test_max_repair_zero_disables_the_loop(self):
        bad = _result("Tel: 04-2326-2888\nian@sprayway.com", email="ghost@nowhere.com")
        fake = _FakeVision(bad)
        out = _run(fake, CLOUD_MAX_REPAIR=0)
        assert fake.repair_calls == 0
        assert out.issues


class TestRegexBlindSpots:
    """regex 是台灣導向的，對外國格式有盲點。

    區分兩種情況的規則：值有出現在模型的轉錄裡 → regex 不認得格式（跳過）；
    值不在轉錄裡 → 漏抄或捏造（保留）。
    """

    def test_foreign_format_present_in_transcription_is_skipped(self):
        # 實測：德式 089/12 34 56-0，regex 只抓到 "12 34 56-0"（漏了區碼）→ membership 失敗，
        # 但號碼確實印在卡片上、也確實在轉錄裡 → 不該報 issue、不該白花 repair
        fake = _FakeVision(_result(
            "Acme GmbH\nTelefon 089/12 34 56-0",
            company_name="Acme GmbH", phone="089/12 34 56-0",
        ))
        out = _run(fake, CLOUD_MAX_REPAIR=1)
        assert out.issues == []
        assert fake.repair_calls == 0

    def test_value_absent_from_transcription_is_still_flagged(self):
        # 這是這層把關真正要抓的：欄位有值但轉錄裡完全沒有 → 漏抄或捏造
        fake = _FakeVision(_result(
            "John Smith\nAcme Corp", phone="+1 (555) 019-2837",
        ))
        out = _run(fake, CLOUD_MAX_REPAIR=0)
        assert [i["field"] for i in out.issues] == ["phone"]

    def test_hallucinated_email_is_flagged(self):
        fake = _FakeVision(_result(
            "Tel: 04-2326-2888\nian@sprayway.com", email="ghost@nowhere.com",
        ))
        out = _run(fake, CLOUD_MAX_REPAIR=0)
        assert [i["field"] for i in out.issues] == ["email"]

    def test_conflict_issues_survive_the_filter(self):
        # 同一號碼同時填進 mobile 與 fax —— 與轉錄品質無關，必須保留
        fake = _FakeVision(_result(
            "Tel 04-2326-2888", mobile="04-2326-2888", fax="04-2326-2888",
        ))
        out = _run(fake, CLOUD_MAX_REPAIR=0)
        assert out.issues, "跨欄位重複必須被回報"


class TestCouplingGuards:
    def test_snap_candidates_is_importable(self):
        """守住對 app.services.card_extractor._snap_candidates 這個 private symbol 的耦合。"""
        from app.services.card_extractor import _snap_candidates

        assert callable(_snap_candidates)

    def test_pipeline_pulls_no_gpu_dependencies(self):
        """機械化執行「無 GPU 依賴」的宣稱，也等於驗證 Dockerfile 的 COPY 清單。

        必須開全新的直譯器：同進程斷言會失敗，因為 tests/test_ocr_adapter.py
        早已在同一個 session 裡 import 過 opencc。
        """
        code = (
            "import cloud.services.card_pipeline, sys;"
            "bad={m for m in sys.modules if m.split('.')[0] in "
            "{'mineru','opencc','torch','ollama','paddleocr'}};"
            "assert not bad, bad"
        )
        subprocess.run([sys.executable, "-c", code], check=True, cwd=os.getcwd())


class TestConfigLoadOrder:
    """最高風險項：cloud.config 必須在 app.database 之前把 .env.cloud 灌進 os.environ，

    否則根目錄 .env 的 DATABASE_URL 會勝出，雲端管線會安靜地寫進 GPU 管線的資料庫。
    """

    def test_cloud_env_wins_over_root_env(self, tmp_path):
        env_file = tmp_path / "env.cloud"
        env_file.write_text("DATABASE_URL=sqlite:///./__cloud_order_probe__.db\n")

        code = textwrap.dedent("""
            from cloud import config          # 先載入 .env.cloud
            from app.database import engine   # 才輪到 app 讀 DATABASE_URL
            print(engine.url)
        """)
        env = {**os.environ, "CLOUD_ENV_FILE": str(env_file)}
        env.pop("DATABASE_URL", None)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=os.getcwd(), env=env, check=True)
        assert "__cloud_order_probe__.db" in out.stdout, (
            "cloud.config 的 .env.cloud 沒有勝出——載入順序壞了"
        )


class TestArchiveExtension:
    """存檔副檔名由「PIL 偵測到的實際格式」決定，不信任使用者傳來的檔名。

    上傳的 content-type 與檔名都可能說謊（手機常把 HEIC 標成 octet-stream）。
    /media 是 StaticFiles，靠副檔名猜 MIME——副檔名錯了瀏覽器就渲染不出來。
    """

    def test_detected_format_wins_over_filename(self):
        from cloud.routers.cards import _archive_extension

        assert _archive_extension("PNG") == "png"
        assert _archive_extension("JPEG") == "jpg"
        assert _archive_extension("WEBP") == "webp"

    def test_unknown_or_missing_format_falls_back_to_jpg(self):
        from cloud.routers.cards import _archive_extension

        assert _archive_extension(None) == "jpg"
        assert _archive_extension("HEIF") == "jpg", "HEIC 存的是 JPEG 轉檔，副檔名必須跟著改"

    def test_heic_is_advertised_only_when_decodable(self):
        """宣告一個容器解不了的 MIME，會把乾淨的 400 變成解碼階段的 500。"""
        from cloud.routers.cards import _allowed_content_types
        from cloud.services.image_prep import HEIC_SUPPORTED

        allowed = _allowed_content_types()
        assert {"image/jpeg", "image/png", "image/webp"} <= allowed
        assert ("image/heic" in allowed) is bool(HEIC_SUPPORTED and os.environ.get(
            "CLOUD_ALLOW_HEIC", "true").lower() in ("1", "true", "yes", "on"))


class TestEmptyEnvVarsAreCleared:
    """`.env` 把選填項寫成 `OPENAI_BASE_URL=` 時，dotenv 會放進一個空字串。

    OpenAI SDK 在 base_url=None 時會自己去讀這個環境變數，空字串會被當成合法的
    base URL → httpx.UnsupportedProtocol，但表面症狀是 APIConnectionError，極難查。
    cloud/config.py 在 load_dotenv 之後把空值移除；這個測試守住它。
    """

    def test_empty_base_url_is_removed_from_environ(self, tmp_path):
        env_file = tmp_path / "env.cloud"
        env_file.write_text("OPENAI_API_KEY=sk-test\nOPENAI_BASE_URL=\n")

        code = textwrap.dedent("""
            import os
            from cloud import config
            print("PRESENT" if "OPENAI_BASE_URL" in os.environ else "REMOVED")
            print(config.base_url())
        """)
        env = {**os.environ, "CLOUD_ENV_FILE": str(env_file)}
        env.pop("OPENAI_BASE_URL", None)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=os.getcwd(), env=env, check=True).stdout.split()
        assert out[0] == "REMOVED", "空的 OPENAI_BASE_URL 必須從 os.environ 移除"
        assert out[1] == "None"

    def test_real_base_url_survives(self, tmp_path):
        env_file = tmp_path / "env.cloud"
        env_file.write_text("OPENAI_BASE_URL=https://gateway.example.com/v1\n")
        code = "from cloud import config; print(config.base_url())"
        env = {**os.environ, "CLOUD_ENV_FILE": str(env_file)}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=os.getcwd(), env=env, check=True).stdout.strip()
        assert out == "https://gateway.example.com/v1"
