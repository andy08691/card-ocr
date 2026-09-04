"""
tests/test_cloud_vision.py — 雲端 VLM 層的 prompt / schema / 錯誤映射（離線）

全部是純函式與 schema 檢查，不建 client、不發網路請求、不需要 API key。
"""

import json

import pytest

from app.schemas.card import CardBase
from app.schemas.extraction import CardExtraction
from cloud.schemas.vision import CardVisionResult
from cloud.services import vision_extractor as vx

_TEN_KEYS = {
    "company_name", "person_name", "english_name", "job_title", "email",
    "phone", "mobile", "fax", "address", "website",
}


class TestSchemaContract:
    """防 drift 的真正護欄：10 欄位必須永遠是同一組。"""

    def test_single_source_of_truth(self):
        assert set(CardExtraction.model_fields) == set(CardBase.model_fields) == _TEN_KEYS

    def test_vision_result_puts_transcription_first(self):
        # structured output 照 schema 順序生成，raw_text 必須排在 fields 前面，
        # 才能「先抄一遍、再從自己抄的內容填欄位」（接地）。
        assert list(CardVisionResult.model_fields) == ["raw_text", "fields"]

    def test_sdk_generates_strict_schema(self):
        from openai.lib._pydantic import to_strict_json_schema

        s = to_strict_json_schema(CardVisionResult)
        assert s["additionalProperties"] is False
        assert set(s["required"]) == {"raw_text", "fields"}

        inner = s["$defs"]["CardExtraction"]
        assert inner["additionalProperties"] is False
        assert set(inner["required"]) == _TEN_KEYS, (
            "SDK 必須把 10 個欄位全標 required；若哪天不再如此，"
            "就得像 llm_parser._extraction_schema() 那樣手動補"
        )

    def test_parses_canned_json_to_exactly_ten_keys(self):
        payload = json.dumps({
            "raw_text": "高都汽車股份有限公司\n陳志均\n電話（04）2326-2888",
            "fields": {"company_name": "高都汽車股份有限公司", "person_name": "陳志均",
                       "english_name": None, "job_title": None, "email": None,
                       "phone": "（04）2326-2888", "mobile": None, "fax": None,
                       "address": None, "website": None},
        })
        r = CardVisionResult.model_validate_json(payload)
        assert set(r.fields.model_dump()) == _TEN_KEYS
        assert r.fields.email is None
        assert r.fields.phone == "（04）2326-2888"


class TestSystemPrompt:
    """SYSTEM_PROMPT 的承重規則——改壞了整條管線的品質就垮了。"""

    def test_forbids_invention_and_requires_null(self):
        p = vx.SYSTEM_PROMPT
        assert "Never invent" in p
        assert "null" in p

    def test_mentions_phone_fax_mobile_labels(self):
        p = vx.SYSTEM_PROMPT
        for label in ("Tel", "Mobile", "Fax", "手機", "傳真"):
            assert label in p

    def test_requires_character_for_character_copy(self):
        # 這條是承重牆：它決定重用的 validator 嚴格值比對是有意義的檢查，
        # 還是一台誤報製造機。
        assert "character-for-character" in vx.SYSTEM_PROMPT
        assert "（04）2326-2888" in vx.SYSTEM_PROMPT

    def test_requires_line_by_line_transcription(self):
        assert "line-by-line transcription" in vx.SYSTEM_PROMPT

    def test_tells_model_to_ignore_background(self):
        # 手機拍照時名片常只佔畫面一部分
        assert "ignore the table surface" in vx.SYSTEM_PROMPT

    def test_forbids_simplified_traditional_conversion(self):
        assert "Simplified and Traditional" in vx.SYSTEM_PROMPT


class TestContentBuilders:
    """content 的順序是承重的：圖片必須在文字前面（prompt cache 前綴）。"""

    def test_user_content_puts_image_before_text(self):
        c = vx.build_user_content("data:image/jpeg;base64,AAA", "auto")
        assert [p["type"] for p in c] == ["input_image", "input_text"], (
            "圖片必須在前——快取比對的是前綴，文字在前會讓同一張卡的 repair "
            "在文字就分岔，圖片永遠進不了共用前綴"
        )
        assert c[0]["image_url"] == "data:image/jpeg;base64,AAA"
        assert c[0]["detail"] == "auto", "detail 是 SDK 的 Required 欄位，必須每次明確傳"

    def test_repair_content_still_includes_the_image(self):
        # 這是與 llm_parser 純文字 repair 的關鍵差異：模型要能再看一眼圖片。
        # 守住它，不讓日後有人把它悄悄退化成純文字。
        c = vx.build_repair_content(
            "data:image/jpeg;base64,AAA",
            {"email": "wrong@x.com"},
            [{"field": "email", "message": "email not among OCR candidates"}],
            "raw transcription", {"emails": ["right@x.com"]}, "auto",
        )
        assert [p["type"] for p in c] == ["input_image", "input_text"]
        assert c[0]["image_url"] == "data:image/jpeg;base64,AAA"

    def test_extract_and_repair_share_the_image_prefix(self):
        """extract 與 repair 的第 0 個 part 必須完全相同，才可能命中長前綴快取。"""
        url = "data:image/jpeg;base64,AAA"
        a = vx.build_user_content(url, "auto")
        b = vx.build_repair_content(url, {}, [], "raw", {}, "auto")
        assert a[0] == b[0]

    def test_repair_content_carries_issues_and_previous(self):
        c = vx.build_repair_content(
            "data:...", {"email": "wrong@x.com"},
            [{"field": "email", "message": "email not among OCR candidates"}],
            "raw", {"emails": ["right@x.com"]}, "auto",
        )
        text = c[1]["text"]
        assert "email" in text and "not among OCR candidates" in text
        assert "wrong@x.com" in text and "right@x.com" in text
        assert "Do not invent" in text


class TestPromptCacheKey:
    """prompt_cache_key 影響路由。每張卡一個 key 等於把快取切開、保證永不命中。

    固定值讓所有請求共用 instructions + strict schema 前綴（實測 1,424 tokens，
    高於 GPT-5.6 系列 1,024 的門檻），費率從 cache write $0.25/1M 降到 $0.02/1M。
    """

    def test_default_key_is_a_constant(self):
        assert isinstance(vx._CACHE_KEY, str) and vx._CACHE_KEY

    def test_extractor_falls_back_to_the_shared_key(self):
        captured = {}

        class _Probe(vx.VisionCardExtractor):
            async def _respond(self, content, cache_key=None):
                captured["key"] = cache_key
                return None

        class _Prepared:
            data_url = "data:image/jpeg;base64,AAA"

        import asyncio

        asyncio.run(_Probe().extract(_Prepared()))
        assert captured["key"] == vx._CACHE_KEY, "沒傳 cache_key 時必須落回共用的固定值"


class _Stub:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestCheckResponse:
    def test_ok_response_passes(self):
        vx.check_response(_Stub(status="completed", output=[], output_parsed=object()))

    def test_incomplete_max_tokens_maps_to_422_with_the_fix(self):
        with pytest.raises(vx.VisionExtractionError) as e:
            vx.check_response(_Stub(status="incomplete",
                                    incomplete_details=_Stub(reason="max_output_tokens")))
        assert e.value.status_code == 422
        assert "CLOUD_MAX_OUTPUT_TOKENS" in e.value.detail

    def test_refusal_maps_to_422(self):
        resp = _Stub(status="completed", output_parsed=None,
                     output=[_Stub(content=[_Stub(type="refusal", refusal="nope")])])
        with pytest.raises(vx.VisionExtractionError) as e:
            vx.check_response(resp)
        assert e.value.status_code == 422
        assert "refused" in e.value.detail

    def test_unparsed_output_maps_to_422(self):
        with pytest.raises(vx.VisionExtractionError) as e:
            vx.check_response(_Stub(status="completed", output=[], output_parsed=None))
        assert e.value.status_code == 422


def _api_error(cls, status):
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    resp = httpx.Response(status, request=req)
    return cls("boom", response=resp, body=None)


class TestProviderErrorMapping:
    """金鑰／權限問題是我方設定錯 → 5xx；圖片不被接受 → 422（對齊 app/ 的語意）。"""

    def test_rate_limit_is_503_with_retry_after(self):
        import openai

        err = vx.map_provider_error(_api_error(openai.RateLimitError, 429))
        assert err.status_code == 503
        assert err.retry_after

    def test_auth_failure_is_502_not_401(self):
        import openai

        assert vx.map_provider_error(_api_error(openai.AuthenticationError, 401)).status_code == 502

    def test_bad_request_is_422(self):
        import openai

        assert vx.map_provider_error(_api_error(openai.BadRequestError, 400)).status_code == 422

    def test_internal_server_error_is_502(self):
        import openai

        assert vx.map_provider_error(_api_error(openai.InternalServerError, 500)).status_code == 502

    def test_timeout_is_504(self):
        import httpx
        import openai

        exc = openai.APITimeoutError(request=httpx.Request("POST", "https://x"))
        assert vx.map_provider_error(exc).status_code == 504

    def test_connection_error_is_503(self):
        import httpx
        import openai

        exc = openai.APIConnectionError(request=httpx.Request("POST", "https://x"))
        assert vx.map_provider_error(exc).status_code == 503

    def test_unknown_error_is_502(self):
        assert vx.map_provider_error(ValueError("???")).status_code == 502
