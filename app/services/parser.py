import re
from typing import Optional


# ── Universal regex patterns ──────────────────────────────────────────────────

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
MOBILE_TW_RE = re.compile(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}")
# Phone: (02)2326-2888 / (02) 8666-8800 / 07-782 7271 / 04-2326-2888
PHONE_TW_RE = re.compile(r"[\(（]?0\d{1,2}[\)） ]\s*[-\s]?\d{3,4}[-\s]?\d{4}")
PHONE_INTL_RE = re.compile(r"\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3,9}")
PHONE_FREE_RE = re.compile(r"0800[-\s]?\d{3}[-\s]?\d{3}")  # 0800 免費電話
WEBSITE_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
FAX_LINE_RE = re.compile(r"傳真[：:]\s*([\S]+)")  # 傳真標籤

# English label-based phone extraction
# Uses MULTILINE so ^ anchors to line start — prevents "Head Office:..." from matching
# Separator is optional to handle OCR merging (e.g. "Office604.960.3231")
EN_TEL_LABEL_RE = re.compile(
    r"^(?:Tel(?:ephone)?|Phone|Ph|Office|Work|O|T)\s*[\.:\|]?\s*([\+\d][\d\s.\-\(\)]{6,})",
    re.IGNORECASE | re.MULTILINE,
)
EN_MOBILE_LABEL_RE = re.compile(
    r"(?:Mobile|Cell(?:ular)?|M|Mob|手機)\s*[\.:\|]\s*([\+\d][\d\s.\-\(\)]{6,})",
    re.IGNORECASE,
)
EN_FAX_LABEL_RE = re.compile(
    r"(?:Fax|F)\s*[\.:\|]\s*([\+\d][\d\s.\-\(\)]{6,})",
    re.IGNORECASE,
)
# General phone number: 7-15 digits with common separators (fallback)
EN_PHONE_GENERAL_RE = re.compile(
    r"(?<!\d)(\+?\d[\d\s.\-\(\)]{6,14}\d)(?!\d)"
)

# ── Chinese card patterns ─────────────────────────────────────────────────────

# 主品牌 suffix（優先）
ZH_COMPANY_MAIN_SUFFIXES = re.compile(
    r"(股份有限公司|有限公司|集團|控股|企業|工業|科技|事務所|辦事處|分公司"
    r"|房屋|仲介|地產|建設|保險|金融|銀行|證券|投資|顧問公司)"
)
# 分店 suffix（降低優先序）
ZH_COMPANY_BRANCH_SUFFIXES = re.compile(
    r"(加盟店|直營店|捷運店|營業所|分行|分店|門市)"
)
# 合併（用於標記 used）
ZH_COMPANY_SUFFIXES = re.compile(
    r"(股份有限公司|有限公司|集團|控股|企業|工業|科技|事務所|辦事處|分公司"
    r"|房屋|仲介|地產|建設|保險|金融|銀行|證券|投資|顧問公司"
    r"|加盟店|直營店|捷運店|營業所|分行|分店|門市)"
)
ZH_ADDRESS_RE = re.compile(
    r"[\d\s]*[\u4e00-\u9fff]*(市|縣|區|鄉|鎮)([\u4e00-\u9fff0-9\s,#段-]*(路|街|巷|弄|號|樓|棟)[0-9\u4e00-\u9fff]*)"
)
ZH_JOB_TITLE_KEYWORDS = [
    # 管理職
    "總裁", "執行長", "董事長", "董事", "總經理", "副總", "總監", "協理",
    "經理", "副理", "主任", "主管", "組長", "專員",
    # 專業職
    "工程師", "設計師", "顧問", "研究員", "分析師", "規劃師",
    # 業務/房仲
    "業務", "房仲", "經紀人", "聯絡人", "超級人", "理財專員", "服務專員",
    # 行政
    "會計", "行銷", "助理", "秘書", "行政", "客服",
    # 其他
    "代理", "特助", "執行",
]

# ── English card patterns ─────────────────────────────────────────────────────

EN_COMPANY_SUFFIXES = re.compile(
    r"\b(Corp\.?|Inc\.?|Ltd\.?|LLC|Co\.?|Group|Holdings|Technologies|Solutions"
    r"|Consulting|Associates|Realty|Real\s*Estate)\b",
    re.IGNORECASE,
)
EN_ADDRESS_RE = re.compile(
    r"\d[\d\-]*\s+[\w\s]+(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr"
    r"|Lane|Ln|Way|Court|Ct|Highway|Hwy|Parkway|Pkwy|Plaza|Square|Sq)[\w\s,#.]*",
    re.IGNORECASE,
)
EN_JOB_TITLE_KEYWORDS = [
    "CEO", "CTO", "CFO", "COO", "CMO", "President", "Vice President", "VP",
    "Director", "Manager", "Supervisor", "Engineer", "Designer", "Consultant",
    "Analyst", "Advisor", "Associate", "Executive", "Officer", "Lead", "Head",
    "Specialist", "Coordinator", "Representative", "Agent",
]
# Matches Title Case ("Nancy Hoo") and ALL CAPS ("NANCY HOO"), with optional middle initial
EN_NAME_RE = re.compile(r"^[A-Z][a-zA-Z'-]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zA-Z'-]+$")

# Nickname pattern: 童敏惠（小敏）→ strip （...）
NICKNAME_RE = re.compile(r"[（(][\u4e00-\u9fff\w]+[）)]")


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_card(raw_text: str, boxes: list, lang: str = "zh") -> dict:
    """Parse business card OCR output into structured fields."""
    sorted_boxes = sorted(boxes, key=lambda b: _box_top(b["bbox"]))
    sorted_lines = [b["text"].strip() for b in sorted_boxes if b["text"].strip()]

    result = {
        "email": _extract_email(raw_text),
        "mobile": _extract_mobile(raw_text),
        "phone": _extract_phone(raw_text),
        "website": _extract_website(raw_text),
    }

    if lang == "zh":
        result.update(_parse_zh(raw_text, sorted_lines))
    else:
        result.update(_parse_en(raw_text, sorted_lines))

    return result


def _box_top(bbox) -> float:
    try:
        return min(pt[1] for pt in bbox)
    except Exception:
        return 0.0


# ── Universal extractors ──────────────────────────────────────────────────────

def _extract_email(text: str) -> Optional[str]:
    m = EMAIL_RE.search(text)
    return m.group(0) if m else None


def _extract_mobile(text: str) -> Optional[str]:
    # Taiwan mobile (09xx)
    m = MOBILE_TW_RE.search(text)
    if m:
        return re.sub(r"[-\s]", "", m.group(0))
    # English label: Mobile / Cell
    m = EN_MOBILE_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _extract_phone(text: str) -> Optional[str]:
    # Strip fax lines
    lines_without_fax = "\n".join(
        line for line in text.splitlines()
        if not re.search(r"傳真|Fax|FAX", line, re.IGNORECASE)
        and not EN_FAX_LABEL_RE.match(line.strip())
    )

    # International format (+country code)
    m = PHONE_INTL_RE.search(lines_without_fax)
    if m:
        return m.group(0).strip()

    # Taiwan landline
    for m in PHONE_TW_RE.finditer(lines_without_fax):
        number = re.sub(r"[-\s（）()\s]", "", m.group(0))
        if not number.startswith("09") and not number.startswith("0800"):
            return m.group(0).strip()

    # English label: Tel / Office / Phone
    m = EN_TEL_LABEL_RE.search(lines_without_fax)
    if m:
        return m.group(1).strip()

    # General fallback: first 7-15 digit number not matching mobile
    for m in EN_PHONE_GENERAL_RE.finditer(lines_without_fax):
        digits = re.sub(r"\D", "", m.group(1))
        if not digits.startswith("09") and 7 <= len(digits) <= 15:
            return m.group(1).strip()

    return None


def _extract_website(text: str) -> Optional[str]:
    m = WEBSITE_RE.search(text)
    return m.group(0) if m else None


# ── Chinese card parser ───────────────────────────────────────────────────────

def _parse_zh(raw_text: str, lines: list) -> dict:
    result = {
        "company_name": None,
        "person_name": None,
        "english_name": None,
        "job_title": None,
        "address": None,
    }

    used = set()

    # Company name: prefer main brand suffix over branch suffix
    for i, line in enumerate(lines):
        if ZH_COMPANY_MAIN_SUFFIXES.search(line):
            if result["company_name"] is None:
                result["company_name"] = line
            used.add(i)
    for i, line in enumerate(lines):
        if ZH_COMPANY_BRANCH_SUFFIXES.search(line):
            used.add(i)  # mark as used but don't override main company

    # Job title: line containing known keywords
    for i, line in enumerate(lines):
        if i in used:
            continue
        for kw in ZH_JOB_TITLE_KEYWORDS:
            if kw in line:
                result["job_title"] = line
                used.add(i)
                break
        if result["job_title"]:
            break

    # Address: line by line, strip label prefix (地址：/ 地址)
    _addr_label = re.compile(r"^(地址[：:]?\s*|住址[：:]?\s*)")
    for i, line in enumerate(lines):
        if i in used:
            continue
        if ZH_ADDRESS_RE.search(line):
            result["address"] = _addr_label.sub("", line).strip()
            used.add(i)
            break

    # English name
    for i, line in enumerate(lines):
        if i in used:
            continue
        if EN_NAME_RE.match(line):
            result["english_name"] = line
            used.add(i)
            break

    # Person name: short CJK line (2–5 chars), strip nickname in parens
    for i, line in enumerate(lines):
        if i in used:
            continue
        clean = NICKNAME_RE.sub("", line).strip()
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", clean)
        if 2 <= len(cjk_chars) <= 5 and len(clean) <= 6:
            result["person_name"] = clean
            used.add(i)
            break

    return result


# Strip common English address label prefixes (e.g. "Head Office: ", "Address: ")
_EN_ADDR_LABEL_RE = re.compile(
    r"^(?:Head\s+)?(?:Office|Address|Addr|Location|HQ|Headquarters)\s*[:\s]+",
    re.IGNORECASE,
)
# Canadian/US city-province/state-postal pattern for multi-line address concat
_EN_CITY_LINE_RE = re.compile(
    r"[A-Za-z\s]+,\s*[A-Z]{2}|\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b|\b\d{5}(?:-\d{4})?\b"
)
# All-caps brand name (single word or brand with apostrophe/hyphen, ≤ 3 words)
_EN_BRAND_RE = re.compile(r"^[A-Z][A-Z0-9'.\-]+(?:\s+[A-Z][A-Z0-9'.\-]+){0,2}$")


# ── English card parser ───────────────────────────────────────────────────────

def _parse_en(raw_text: str, lines: list) -> dict:
    result = {
        "company_name": None,
        "person_name": None,
        "english_name": None,
        "job_title": None,
        "address": None,
    }

    used = set()

    # 1. Company: lines with known corporate suffixes
    for i, line in enumerate(lines):
        if EN_COMPANY_SUFFIXES.search(line):
            if result["company_name"] is None:
                result["company_name"] = line
            used.add(i)

    # 2. Job title
    for i, line in enumerate(lines):
        if i in used:
            continue
        line_upper = line.upper()
        for kw in EN_JOB_TITLE_KEYWORDS:
            if kw.upper() in line_upper:
                result["job_title"] = line
                used.add(i)
                break
        if result["job_title"]:
            break

    # 3. Address — strip label prefix, concat next line if city/postal
    for i, line in enumerate(lines):
        if i in used:
            continue
        clean = _EN_ADDR_LABEL_RE.sub("", line).strip()
        if EN_ADDRESS_RE.search(clean):
            # Try to append the next line if it looks like city/province/zip
            if i + 1 < len(lines) and (i + 1) not in used:
                next_line = lines[i + 1]
                if _EN_CITY_LINE_RE.search(next_line):
                    clean = clean + ", " + next_line
                    used.add(i + 1)
            result["address"] = clean
            used.add(i)
            break

    # 4. Person name — detect before company fallback so all-caps names are marked used
    for i, line in enumerate(lines):
        if i in used:
            continue
        if EN_NAME_RE.match(line):
            result["person_name"] = line
            result["english_name"] = line
            used.add(i)
            break

    # 5. Company fallback: all-caps brand name (e.g. "ARC'TERYX", "NIKE")
    if result["company_name"] is None:
        for i, line in enumerate(lines):
            if i in used:
                continue
            stripped = line.strip()
            if _EN_BRAND_RE.match(stripped) and len(stripped) <= 30:
                result["company_name"] = stripped
                used.add(i)
                break

    return result
