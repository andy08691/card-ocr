"""
services/parser.py — 名片欄位解析器

接收 OCR 回傳的原始文字（raw_text）與文字區塊清單（boxes），
解析出結構化欄位：公司名、姓名、職稱、電話、地址等。

解析策略：
  1. 通用欄位（email、mobile、phone、fax、website）：正規表達式直接從 raw_text 比對
  2. 語言特定欄位（company_name、person_name、job_title、address）：
     - 中文（lang="zh"）→ _parse_zh()
     - 英文（lang="en"）→ _parse_en()
  3. boxes 依 Y 座標（由上到下）排序，讓欄位解析符合閱讀順序

調整精度的主要方向：
  - 新增常見公司後綴   → EN_COMPANY_SUFFIXES 或 ZH_COMPANY_MAIN_SUFFIXES
  - 新增職稱關鍵字     → ZH_JOB_TITLE_KEYWORDS 或 EN_JOB_TITLE_KEYWORDS
  - 新增地址關鍵字     → EN_ADDRESS_RE 或 ZH_ADDRESS_RE
  - 調整個人信箱排除清單 → _PERSONAL_DOMAINS
"""

import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# 通用正規表達式（不分語言）
# ══════════════════════════════════════════════════════════════════════════════

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)

MOBILE_TW_RE = re.compile(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}")

# 台灣市話：(02)2326-2888 / 07-782 7271 / 04-2326-2888
# 長邊分隔符號（括號 / 破折號 / 空白）必須存在，避免比對到郵遞區號
PHONE_TW_RE = re.compile(r"[\(（]?0\d{1,2}[\)）\-\s]\s*\d{3,4}[-\s]?\d{4}")

# 國際電話：+1-604-960-3231 / +44(0)1613665020
PHONE_INTL_RE = re.compile(r"\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3,9}")

PHONE_FREE_RE = re.compile(r"0800[-\s]?\d{3}[-\s]?\d{3}")  # 台灣免費電話

# 網址：不捕捉結尾的標點符號（! . , 等）
WEBSITE_RE = re.compile(r"(https?://[^\s>\"',!]+|www\.[^\s>\"',!]+)", re.IGNORECASE)

# 中文傳真標籤
FAX_ZH_RE = re.compile(r"傳真\s*[：:]?\s*([\+\d][\d\s.\-\(\)]{5,}\d)")

# ── 英文電話標籤（MULTILINE 模式讓 ^ 對應每行開頭）────────────────────────
# 分隔符號設為 optional，處理 OCR 合字（如 "Office604.960.3231"）
EN_TEL_LABEL_RE = re.compile(
    r"^(?:Tel(?:ephone)?|Phone|Ph|Office|Work|O|T)\s*[\.:\|]?\s*([\+\d][\d\s.\-\(\)]{6,})",
    re.IGNORECASE | re.MULTILINE,
)
EN_MOBILE_LABEL_RE = re.compile(
    r"^(?:Mobile|Cell(?:ular)?|M|Mob|手機)\s*[\.:\|]?\s*([\+\d][\d\s.\-\(\)]{6,})",
    re.IGNORECASE | re.MULTILINE,
)
EN_FAX_LABEL_RE = re.compile(
    r"(?:Fax|F)\s*[\.:\|]\s*([\+\d][\d\s.\-\(\)]{6,})",
    re.IGNORECASE,
)

# 通用電話號碼（7–15 位數字加常見分隔符），作為最後備援
EN_PHONE_GENERAL_RE = re.compile(r"(?<!\d)(\+?\d[\d\s.\-\(\)]{6,14}\d)(?!\d)")


# ══════════════════════════════════════════════════════════════════════════════
# 中文名片 patterns
# ══════════════════════════════════════════════════════════════════════════════

# 主品牌後綴（優先匹配，作為 company_name）
ZH_COMPANY_MAIN_SUFFIXES = re.compile(
    r"(股份有限公司|有限公司|集團|控股|企業|工業|科技|事務所|辦事處|分公司"
    r"|房屋|仲介|地產|建設|保險|金融|銀行|證券|投資|顧問公司)"
)

# 分支機構後綴（標記為已使用但不作為 company_name，避免「鳳林管業所」被誤認為人名）
ZH_COMPANY_BRANCH_SUFFIXES = re.compile(
    r"(加盟店|直營店|捷運店|營業所|管業所|分行|分店|門市)"
)

# 合集（用於其他需要判斷「是否公司相關」的場景）
ZH_COMPANY_SUFFIXES = re.compile(
    r"(股份有限公司|有限公司|集團|控股|企業|工業|科技|事務所|辦事處|分公司"
    r"|房屋|仲介|地產|建設|保險|金融|銀行|證券|投資|顧問公司"
    r"|加盟店|直營店|捷運店|營業所|管業所|分行|分店|門市)"
)

# 中文地址：需包含縣市區鄉鎮 + 路街巷弄號樓棟
ZH_ADDRESS_RE = re.compile(
    r"[\d\s]*[\u4e00-\u9fff]*(市|縣|區|鄉|鎮)([\u4e00-\u9fff0-9\s,#段-]*(路|街|巷|弄|號|樓|棟)[0-9\u4e00-\u9fff]*)"
)

# 職稱關鍵字清單（部分比對，只要行內含此字即視為職稱行）
# 注意：客服、執行等常見字也可能出現在非職稱行，需搭配「數字序列過濾」避免誤判
ZH_JOB_TITLE_KEYWORDS = [
    # 管理職
    "總裁", "執行長", "董事長", "董事", "總經理", "副總", "總監", "協理",
    "經理", "副理", "主任", "主管", "組長", "專員",
    # 專業職
    "工程師", "設計師", "顧問", "研究員", "分析師", "規劃師",
    # 業務 / 房仲
    "業務", "房仲", "經紀人", "聯絡人", "超級人", "理財專員", "服務專員",
    # 行政
    "會計", "行銷", "助理", "秘書", "行政", "客服",
    # 其他
    "代理", "特助", "執行",
]


# ══════════════════════════════════════════════════════════════════════════════
# 英文名片 patterns
# ══════════════════════════════════════════════════════════════════════════════

# 公司後綴：leading boundary 用 (?:\b|(?<=[A-Z0-9])) 而非單純 \b
# 原因：OCR 常合併文字，如 "OSCLimited"，此時 \bLimited 無法比對（C 與 L 之間無 word boundary）
EN_COMPANY_SUFFIXES = re.compile(
    r"(?:\b|(?<=[A-Z0-9]))(Corp\.?|Inc\.?|Ltd\.?|Limited|LLC|Co\.?|Group|Holdings"
    r"|Technologies|Solutions|Consulting|Associates|Realty|Real\s*Estate"
    r"|Oy|GmbH|AG|SA|SpA|AB|NV|BV|Pte\.?)(?:\b|$)",
    re.IGNORECASE,
)

# 英文地址：起始須有門牌號碼，街道類型關鍵字含 Suite/Floor/Unit 等
EN_ADDRESS_RE = re.compile(
    r"\d[\d\-]*\s+[\w\s]+(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr"
    r"|Lane|Ln|Way|Court|Ct|Highway|Hwy|Parkway|Pkwy|Plaza|Square|Sq"
    r"|Suite|Ste|Floor|Unit)[\w\s,#.]*",
    re.IGNORECASE,
)

# 英文職稱關鍵字（大小寫不敏感比對）
EN_JOB_TITLE_KEYWORDS = [
    "CEO", "CTO", "CFO", "COO", "CMO", "President", "Vice President", "VP",
    "Director", "Manager", "Supervisor", "Engineer", "Designer", "Consultant",
    "Analyst", "Advisor", "Associate", "Executive", "Officer", "Lead", "Head",
    "Specialist", "Coordinator", "Representative", "Agent",
    "Founder", "Co-Founder", "Partner", "Principal", "Chairman",
    "Secretary", "Treasurer", "Accountant", "Developer", "Architect",
    "Marketing", "Sales", "Procurement", "Buyer", "Planner",
]

# 英文姓名：Title Case 或 ALL CAPS，支援 2–3 個單字（含中間名首字母）
# 例：Nancy Hoo / NANCY HOO / Woo Jeong Hong / John A. Smith
EN_NAME_RE = re.compile(
    r"^[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]*\.?)?(?:\s+[A-Z][a-zA-Z'-]*\.?)?\s+[A-Z][a-zA-Z'-]+$"
)

# 中文姓名括號暱稱，如 童敏惠（小敏）→ 去掉 （小敏）
NICKNAME_RE = re.compile(r"[（(][\u4e00-\u9fff\w]+[）)]")

# 個人信箱域名排除清單（用於 Email domain → company 推斷，避免 "Gmail" 成為公司名）
_PERSONAL_DOMAINS = {
    "gmail", "yahoo", "hotmail", "outlook", "icloud", "qq",
    "163", "126", "live", "msn", "protonmail", "zoho",
}


# ══════════════════════════════════════════════════════════════════════════════
# 輔助函式
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_company(line: str) -> str:
    """修正 OCR 合字造成的公司名後綴無空格問題。

    例：'OSCLimited' → 'OSC Limited'
        'ACMECorp'   → 'ACME Corp'

    只在後綴緊接大寫字母（無空格）時插入空格，正常名稱不受影響。
    """
    return re.sub(
        r"(?<=[A-Z0-9])(Corp\.?|Inc\.?|Ltd\.?|Limited|LLC|Co\.?|Oy|GmbH|AG|SA|SpA|AB|NV|BV|Pte\.?)\b",
        r" \1",
        line,
    )


def _company_from_email_domain(email: str) -> Optional[str]:
    """從 Email domain 推斷公司名稱（最後備援）。

    例：ian.christian@sprayway.com → 'Sprayway'
        woo@nepa.co.kr             → 'Nepa'
        john.doe@gmail.com         → None（個人信箱，不推斷）

    處理多層 ccTLD：先去掉 .co.kr / .com.tw / .org.uk 等，再取最後一段 label。
    """
    if not email or "@" not in email:
        return None
    domain = email.split("@")[1].lower()
    # 去掉 ccTLD（co.kr, com.tw, org.uk ...）
    domain = re.sub(r"\.(co|com|org|net|edu)\.[a-z]{2}$", "", domain)
    # 取最後一個點之前的部分作為公司名
    name = domain.rsplit(".", 1)[0]
    if name in _PERSONAL_DOMAINS:
        return None
    return name.capitalize()


# ══════════════════════════════════════════════════════════════════════════════
# 主要入口
# ══════════════════════════════════════════════════════════════════════════════

def parse_card(raw_text: str, boxes: list, lang: str = "zh") -> dict:
    """解析名片 OCR 輸出，回傳結構化欄位字典。

    Args:
        raw_text: OCR 回傳的原始文字（換行分隔）
        boxes:    [{"text": str, "bbox": [...], "confidence": float}, ...]
        lang:     "zh"（中文名片）或 "en"（英文名片）

    Returns:
        dict，包含所有解析欄位（無法識別的欄位值為 None）
    """
    # 依 Y 座標由上到下排序（名片閱讀順序）
    sorted_boxes = sorted(boxes, key=lambda b: _box_top(b["bbox"]))
    sorted_lines = [b["text"].strip() for b in sorted_boxes if b["text"].strip()]

    # 通用欄位：不分語言，直接從 raw_text 以正規表達式擷取
    result = {
        "email": _extract_email(raw_text),
        "mobile": _extract_mobile(raw_text),
        "phone": _extract_phone(raw_text),
        "fax": _extract_fax(raw_text),
        "website": _extract_website(raw_text),
    }

    # 語言特定欄位
    if lang == "zh":
        result.update(_parse_zh(raw_text, sorted_lines))
    else:
        result.update(_parse_en(raw_text, sorted_lines))

    return result


def _box_top(bbox) -> float:
    """取 box 最小 Y 值作為排序依據（最靠近名片頂部的點）。"""
    try:
        return min(pt[1] for pt in bbox)
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 通用欄位擷取器
# ══════════════════════════════════════════════════════════════════════════════

def _extract_email(text: str) -> Optional[str]:
    m = EMAIL_RE.search(text)
    return m.group(0) if m else None


def _extract_mobile(text: str) -> Optional[str]:
    """擷取手機號碼，優先台灣 09xx 格式，次選英文標籤（M: / Mobile:）。"""
    m = MOBILE_TW_RE.search(text)
    if m:
        return re.sub(r"[-\s]", "", m.group(0))  # 統一去掉分隔符
    m = EN_MOBILE_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _extract_phone(text: str) -> Optional[str]:
    """擷取市話 / 公司電話，自動跳過傳真號碼與手機號碼。

    過濾邏輯：
      1. 含傳真標籤的行：移除傳真數字後繼續（保留同行的市話部分）
         例："Tel:03-3497-8076Fax:03-3497-2258" → 只保留 "Tel:03-3497-8076"
      2. 已找到的手機號碼：在 INTL 比對與通用 fallback 中跳過

    優先序：國際格式（+xx）→ 台灣市話 → 英文標籤（Tel/Office）→ 通用數字 fallback
    """
    # 含傳真標籤的行：只移除傳真數字，保留電話部分
    processed_lines = []
    for line in text.splitlines():
        if re.search(r"傳真|Fax|FAX", line, re.IGNORECASE):
            stripped = re.sub(
                r"(?:傳真|Fax|FAX)\s*[：:\|]?\s*[\d\s.\-\(\)\+]+",
                "", line, flags=re.IGNORECASE
            ).strip()
            if stripped:
                processed_lines.append(stripped)
        else:
            processed_lines.append(line)
    lines_without_fax = "\n".join(processed_lines)

    # 取得手機號碼供後續去重
    mobile_pre = _extract_mobile(text)
    mobile_digits_pre = re.sub(r"\D", "", mobile_pre) if mobile_pre else ""

    # 國際格式
    m = PHONE_INTL_RE.search(lines_without_fax)
    if m:
        candidate = m.group(0).strip()
        cdigits = re.sub(r"\D", "", candidate)
        # 跳過「國際格式只比對到手機號碼的前半段」的情況
        if not mobile_digits_pre or not mobile_digits_pre.startswith(cdigits):
            return candidate

    # 台灣市話
    for m in PHONE_TW_RE.finditer(lines_without_fax):
        number = re.sub(r"[-\s（）()\s]", "", m.group(0))
        if not number.startswith("09") and not number.startswith("0800"):
            return m.group(0).strip()

    # 英文標籤（Tel / Office / Phone 等）
    m = EN_TEL_LABEL_RE.search(lines_without_fax)
    if m:
        return m.group(1).strip()

    # 通用數字 fallback（7–15 位，排除手機）
    for m in EN_PHONE_GENERAL_RE.finditer(lines_without_fax):
        digits = re.sub(r"\D", "", m.group(1))
        if not digits.startswith("09") and 7 <= len(digits) <= 15:
            if mobile_digits_pre and (digits == mobile_digits_pre or mobile_digits_pre.startswith(digits)):
                continue
            return m.group(1).strip()

    return None


def _extract_fax(text: str) -> Optional[str]:
    """擷取傳真號碼（中文「傳真」標籤或英文 Fax 標籤）。"""
    m = FAX_ZH_RE.search(text)
    if m:
        return m.group(1).strip()
    m = EN_FAX_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _extract_website(text: str) -> Optional[str]:
    m = WEBSITE_RE.search(text)
    return m.group(0) if m else None


# ══════════════════════════════════════════════════════════════════════════════
# 中文名片解析
# ══════════════════════════════════════════════════════════════════════════════

def _parse_zh(raw_text: str, lines: list) -> dict:
    """解析中文名片的結構化欄位。

    used 集合追蹤「已消耗」的行索引，避免同一行被多個欄位重複擷取。

    解析順序（有依賴關係，不可任意調換）：
      1. 公司名（主後綴 > 分支後綴）
      2. 職稱（關鍵字比對，跳過含電話數字的行）
      3. 地址（正規表達式比對）
      4. 英文名（Title Case / ALL CAPS 比對）
      5. 人名（短 CJK 字串，2–5 個字）
    """
    result = {
        "company_name": None,
        "person_name": None,
        "english_name": None,
        "job_title": None,
        "address": None,
    }
    used = set()

    # ── 1. 公司名 ─────────────────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if ZH_COMPANY_MAIN_SUFFIXES.search(line):
            if result["company_name"] is None:
                result["company_name"] = line
            used.add(i)
    for i, line in enumerate(lines):
        if ZH_COMPANY_BRANCH_SUFFIXES.search(line):
            used.add(i)  # 分支機構行標記為已使用，但不覆蓋主公司名

    # ── 2. 職稱 ───────────────────────────────────────────────────────────────
    # 跳過含電話數字序列的行（避免「顧客服務専線|02-55997299」被誤判為職稱）
    _addr_label = re.compile(r"^(地址[：:]?\s*|住址[：:]?\s*)")
    for i, line in enumerate(lines):
        if i in used:
            continue
        if re.search(r"\d[\d\s.\-\(\)]{5,}\d", line):
            continue
        for kw in ZH_JOB_TITLE_KEYWORDS:
            if kw in line:
                result["job_title"] = line
                used.add(i)
                break
        if result["job_title"]:
            break

    # ── 3. 地址 ───────────────────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if i in used:
            continue
        if ZH_ADDRESS_RE.search(line):
            result["address"] = _addr_label.sub("", line).strip()
            used.add(i)
            break

    # ── 4. 英文名 ─────────────────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if i in used:
            continue
        if EN_NAME_RE.match(line):
            result["english_name"] = line
            used.add(i)
            break

    # ── 5. 人名（中文）────────────────────────────────────────────────────────
    # 條件：2–5 個中文字、行長 ≤ 6 個字元（排除地址 / 公司名等較長的行）
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


# ══════════════════════════════════════════════════════════════════════════════
# 英文名片解析
# ══════════════════════════════════════════════════════════════════════════════

# 英文地址標籤前綴（如 "Head Office: " / "Address: "），比對後去除
_EN_ADDR_LABEL_RE = re.compile(
    r"^(?:Head\s+)?(?:Office|Address|Addr|Location|HQ|Headquarters)\s*[:\s]+",
    re.IGNORECASE,
)

# 城市 / 州省 / 郵遞區號行（用於拼接多行地址）
# 範例：Vancouver, BC / V6B 1A1 / SK14 1RD / 90210
_EN_CITY_LINE_RE = re.compile(
    r"[A-Za-z\s]+,\s*[A-Z]{2}|\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b|\b\d{5}(?:-\d{4})?\b"
)

# 純大寫品牌名（1–3 個單字），作為公司名的最後備援
# 例：ARC'TERYX / NIKE / NORTH FACE
_EN_BRAND_RE = re.compile(r"^[A-Z][A-Z0-9'.\-]+(?:\s+[A-Z][A-Z0-9'.\-]+){0,2}$")


def _parse_en(raw_text: str, lines: list) -> dict:
    """解析英文名片的結構化欄位。

    解析順序（有依賴關係）：
      0. 預標記純 Email / 網址行（避免被誤認為公司名）
      1. 公司名（含後綴的行，OCR 合字自動修正）
      2. 職稱（關鍵字比對）
      3. 地址（門牌 + 街道類型，嘗試拼接下一行城市資訊）
      4. 人名（Title Case / ALL CAPS，排除職稱行）
      4b. 人名備援（從 Email local part 推斷，如 ian.christian → Ian Christian）
      5. 公司名備援（純大寫品牌名，排除與 Email local part 相同的 OCR 合字）
      6. 公司名最後備援（從 Email domain 推斷，如 sprayway.com → Sprayway）
    """
    result = {
        "company_name": None,
        "person_name": None,
        "english_name": None,
        "job_title": None,
        "address": None,
    }
    used = set()

    # 預先計算 Email local part key（用於第 4b 和第 5 步的 OCR 合字判斷）
    # 例：ian.christian@sprayway.com → email_local_key = "ianchristian"
    email_str = _extract_email(raw_text) or ""
    email_local_key = re.sub(r"[.\-_+]", "", email_str.split("@")[0]).lower() if email_str else ""

    # ── 0. 預標記純 Email / 網址行 ────────────────────────────────────────────
    _email_only = re.compile(r"^[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}$", re.IGNORECASE)
    _web_only = re.compile(r"^(https?://\S+|www\.\S+)$", re.IGNORECASE)
    for i, line in enumerate(lines):
        if _email_only.match(line.strip()) or _web_only.match(line.strip()):
            used.add(i)

    # ── 1. 公司名（後綴比對）────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if i in used:
            continue
        if EN_COMPANY_SUFFIXES.search(line):
            if result["company_name"] is None:
                # _normalize_company 修正 OCR 合字（OSCLimited → OSC Limited）
                result["company_name"] = _normalize_company(line)
            used.add(i)

    # ── 2. 職稱 ───────────────────────────────────────────────────────────────
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

    # ── 3. 地址 ───────────────────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if i in used:
            continue
        clean = _EN_ADDR_LABEL_RE.sub("", line).strip()
        if EN_ADDRESS_RE.search(clean):
            # 若下一行看起來像城市 / 郵遞區號，一起拼入地址
            if i + 1 < len(lines) and (i + 1) not in used:
                next_line = lines[i + 1]
                if _EN_CITY_LINE_RE.search(next_line):
                    clean = clean + ", " + next_line
                    used.add(i + 1)
            result["address"] = clean
            used.add(i)
            break

    # ── 4. 人名 ───────────────────────────────────────────────────────────────
    for i, line in enumerate(lines):
        if i in used:
            continue
        if EN_NAME_RE.match(line):
            # 次要防衛：若行內含職稱關鍵字（如 "Senior Engineer"），跳過
            upper = line.upper()
            if any(kw.upper() in upper for kw in EN_JOB_TITLE_KEYWORDS):
                continue
            result["person_name"] = line
            result["english_name"] = line
            used.add(i)
            break

    # ── 4b. 人名備援：從 Email local part 推斷 ───────────────────────────────
    # 觸發條件：步驟 4 未找到人名，且 Email local part 可拆成 2–3 個合法名字段
    # 例：ian.christian → ["ian", "christian"] → "Ian Christian"
    # 條件：每段 ≥ 2 個純英文字母（排除 j.smith 這類只有首字母的情況）
    if result["person_name"] is None and email_str and "@" in email_str:
        local = email_str.split("@")[0]
        parts = re.split(r"[._\-+]", local)
        parts = [p for p in parts if len(p) >= 2 and p.isalpha()]
        if 2 <= len(parts) <= 3:
            inferred = " ".join(p.capitalize() for p in parts)
            result["person_name"] = inferred
            result["english_name"] = inferred

    # ── 5. 公司名備援：純大寫品牌名 ──────────────────────────────────────────
    # 例：ARC'TERYX、NIKE
    # 防衛：若 token 與 Email local part（去分隔符）相同，代表是 OCR 合字的人名，跳過
    # 例：IANCHRISTIAN == ianchristian（來自 ian.christian@...），跳過
    if result["company_name"] is None:
        for i, line in enumerate(lines):
            if i in used:
                continue
            stripped = line.strip()
            if _EN_BRAND_RE.match(stripped) and len(stripped) <= 40:
                candidate_key = re.sub(r"\s", "", stripped).lower()
                if email_local_key and candidate_key == email_local_key:
                    continue  # OCR 合字的人名，不是品牌
                result["company_name"] = stripped
                used.add(i)
                break

    # ── 6. 公司名最後備援：Email domain 推斷 ─────────────────────────────────
    # 僅在前 5 步都找不到公司名時觸發
    # 個人信箱（gmail 等）由 _company_from_email_domain 內部過濾
    if result["company_name"] is None:
        result["company_name"] = _company_from_email_domain(email_str)

    return result
