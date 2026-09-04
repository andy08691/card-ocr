"""
cloud/services/card_pipeline.py — 雲端管線的 workflow

對應 app/services/card_extractor.py:extract_card() 的角色，把
「一次 VLM 呼叫」與「既有的確定性把關」串起來：

    vision.extract()                       # 圖 → {raw_text, 10 欄位}
        ↓
    extract_candidates(raw_text)           # 重用 app/：regex 只找候選，不分類
    validate_card(fields, ...)             # 重用 app/：候選型嚴格驗 + 衝突檢查
        ↓ 有 issue → repair（連圖片一起重送）→ 候選重算 → 再驗，最多 CLOUD_MAX_REPAIR 次
    _snap_candidates(...)                  # 重用 app/：欄位 snap 回名片上印的表面字串
        ↓
    CloudPipelineResult（fields 恰好 10 個 key）

⚠️ 這層把關實際上買到什麼（誠實版）：
raw_text 是「同一個模型」產生的，所以這是**自我一致性**檢查，不是跨引擎交叉檢查。
錯誤是相關的——會幻覺出一組電話的模型，多半也會把它寫進 raw_text，然後檢查就過了。
相對 app/ 那條（MinerU 出文字、Ollama 做分類，兩個獨立引擎）這是實質的削弱。

但它仍然值得，因為代價只是一次 regex、零成本，而且它抓得到真正會發生的失效：
  - 格式漂移：模型把「（04）2326-2888」改寫成「+886-4-2326-2888」。_snap_candidates
    會還原成卡片上印的表面字串，讓 DB 與 GPU 管線對同一張卡的輸出保持一致。光這項就值回票價。
  - 轉錄不完整：欄位有值但 raw_text 漏了 → 抓得到，帶圖的 repair 能真正修好。
  - 跨欄位重複：同一組號碼同時填進 mobile 和 fax → 既有衝突檢查抓得到，
    且這種錯誤與轉錄品質無關（不相關 → 檢查有效）。
  - 公司／地址亂編：鬆散 token 重疊檢查仍抓得到整段捏造。

⚠️ 重用的 regex 是台灣導向（MOBILE_TW_RE / PHONE_TW_RE）加通用國際樣式，
對外國格式會有盲點（實測德式 089/12 34 56-0 只抓到 12 34 56-0，漏了區碼）。
若直接照單全收 validator 的 membership issue，外國名片會產生系統性誤報並白花 repair。

_drop_regex_blind_spots() 用一條更精準的規則區分兩種情況：
  - 值「有」出現在模型的轉錄裡 → regex 只是不認得這個格式 → 跳過（無從驗證）
  - 值「不在」轉錄裡           → 轉錄漏抄了，或模型憑空捏造 → 保留（這正是要抓的）
衝突類 issue（同一號碼佔多個欄位）與轉錄品質無關，一律保留。

要放寬或收緊都改這裡——**絕不要去改 app/services/validator.py**，那是 GPU 管線共用的。
"""

import logging
import re

from app.services.candidate_extractor import extract_candidates
from app.services.card_extractor import _snap_candidates
from app.services.validator import _norm_generic, _norm_phone, validate_card
from cloud import config
from cloud.schemas.vision import CloudPipelineResult
from cloud.services.vision_extractor import VisionCardExtractor

logger = logging.getLogger(__name__)

# app/ 的 ocr_confidence 在 MinerU 下恆為 1.0（README 已聲明此欄位僅為相容保留）。
# 雲端管線同樣沒有 per-field 信心度，比照填 1.0，讓兩條管線的資料列無從分辨。
_CLOUD_OCR_CONFIDENCE = 1.0

# membership issue 的訊息前綴（validator 產生），用來與衝突類 issue 區分
_MEMBERSHIP_MARKER = "not among"
_PHONE_FIELDS = ("phone", "mobile", "fax")
_CANDIDATE_FIELDS = ("email", "website", *_PHONE_FIELDS)

# ── 排版字距正規化 ────────────────────────────────────────────────────────────
# 名片常把中日韓姓名拉開字距排版（「李 建 群」「강 다 원」），那些空白是版面效果、
# 不是名字的一部分。但**絕不能無條件 strip**——實測資料庫裡就有 8 個以上的拉丁字母
# 姓名（Sophie Smith、Ian Christian、Woo Jeong Hong…），那裡的空白是詞的分界，
# 移掉會變成 SophieSmith。連語系也不能當判準：Woo Jeong Hong 是韓文名字的羅馬拼音，
# 與「강 다 원」同語系卻必須保留空白。判準是**書寫系統**。
#
# 規則：只有當「每個空白分隔的 token 都是單一 CJK 字元」時才合併。
# 漢字／假名／諺文是等寬方塊字，字與字之間插空白純粹是字距排版；
# 一旦某個 token 有兩字以上，那個空白就可能是真的分隔符——這也自動保護了
# 日文名片「姓　名」的慣例（「黑澤 啟一」token 各兩字，不會被合併）。
_CJK_CHAR = re.compile(r"[㐀-鿿豈-﫿぀-ヿ가-힯]")

# 只對「模糊型」文字欄位套用。刻意排除：
#   - address：空白是結構性的（門牌、樓層、國名之間）
#   - email / phone / mobile / fax / website：候選型欄位，值必須與名片印刷的表面字串
#     完全一致，_snap_candidates 的保證與 validator 的比對都靠它
_SPACING_FIELDS = ("person_name", "english_name", "company_name", "job_title")

# 壓縮 ASCII 空白，但保留全形空白 U+3000——日文名片的「姓　名」用的就是它，
# 換成半形會改變排版語意。
_ASCII_WS_RUN = re.compile(r"[^\S　]+")


def _normalize_spacing(value):
    """把排版字距合併掉，同時不動真正的詞分界。純函式，見上方規則說明。"""
    if not value:
        return value
    tokens = value.split()
    if len(tokens) >= 2 and all(len(t) == 1 and _CJK_CHAR.match(t) for t in tokens):
        return "".join(tokens)
    return _ASCII_WS_RUN.sub(" ", value).strip()


def _appears_in_transcription(field: str, value: str, raw_text: str) -> bool:
    """值是否確實出現在模型自己的轉錄裡（用與 validator 相同的正規化，語意才會一致）。"""
    if field in _PHONE_FIELDS:
        needle, hay = _norm_phone(value), _norm_phone(raw_text)
    else:
        needle, hay = _norm_generic(value), _norm_generic(raw_text)
    return bool(needle) and needle in hay


def _drop_regex_blind_spots(issues: list, fields, raw_text: str) -> list:
    """濾掉「regex 認不得這個格式」造成的誤報，保留真正的漏抄／捏造與衝突。"""
    d = fields.model_dump()
    kept = []
    for issue in issues:
        is_membership = issue.field in _CANDIDATE_FIELDS and _MEMBERSHIP_MARKER in issue.message
        if is_membership and _appears_in_transcription(issue.field, d.get(issue.field) or "", raw_text):
            logger.info(
                "Skipping %s issue: value is present in the transcription, regex just "
                "did not recognise the format", issue.field,
            )
            continue
        kept.append(issue)
    return kept


async def run_cloud_pipeline(prepared, cache_key=None, extractor=None) -> CloudPipelineResult:
    """跑完整條雲端管線。

    extractor 參數是給測試做依賴注入用的（tests/test_cloud_pipeline.py 塞 _FakeVision
    進來，就能零網路、零花費地驗證整個 workflow）；正式路徑留空自己建。
    """
    vx = extractor or VisionCardExtractor()
    usage = {"calls": 0, "repairs": 0, "input_tokens": 0, "output_tokens": 0,
             "cached_tokens": 0, "reasoning_tokens": 0}

    def _accumulate():
        u = getattr(vx, "last_usage", None) or {}
        usage["calls"] += 1
        for k in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"):
            usage[k] += u.get(k, 0)

    result = await vx.extract(prepared, cache_key)
    _accumulate()

    candidates = extract_candidates(result.raw_text)
    issues = _drop_regex_blind_spots(
        validate_card(result.fields, result.raw_text, candidates), result.fields, result.raw_text
    )

    repairs = 0
    while issues and repairs < config.max_repair():
        repairs += 1
        logger.info("Vision extraction issues, repairing (%d): %s", repairs, [i.field for i in issues])
        result = await vx.repair(
            prepared,
            result.fields.model_dump(),
            [i.model_dump() for i in issues],
            result.raw_text,
            candidates.model_dump(),
            cache_key,
        )
        _accumulate()
        # 關鍵：repair 可能改寫了 raw_text，候選必須重算，
        # 否則等於拿舊尺量新布——會誤判修對的欄位仍然有問題。
        candidates = extract_candidates(result.raw_text)
        issues = _drop_regex_blind_spots(
            validate_card(result.fields, result.raw_text, candidates), result.fields, result.raw_text
        )

    usage["repairs"] = repairs

    if issues:
        # 比照 app/ 的優雅退場：OCR 成功不該被分類失敗拖垮。
        # API 費用已經付了，而且前端有欄位校正 UI——回 10 個欄位中對的 8 個，好過整張失敗。
        logger.warning(
            "Vision extraction still has issues after repair (saving best-effort): %s",
            [(i.field, i.message) for i in issues],
        )

    fields = _snap_candidates(result.fields.model_dump(), candidates)

    # 排版字距正規化。刻意排在 _snap_candidates 之後，且只碰模糊型欄位——
    # 候選型欄位剛被 snap 回名片印刷的表面字串，不能再被動到。
    # 原始未正規化的姓名仍完整保留在 raw_text 裡。
    for field in _SPACING_FIELDS:
        normalized = _normalize_spacing(fields.get(field))
        if normalized != fields.get(field):
            logger.info("Normalized spacing in %s", field)   # 不記值——PII 不進 log
            fields[field] = normalized

    return CloudPipelineResult(
        fields=fields,
        raw_text=result.raw_text,
        ocr_confidence=_CLOUD_OCR_CONFIDENCE,
        issues=[i.model_dump() for i in issues],
        usage=usage,
    )
