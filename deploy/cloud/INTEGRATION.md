# 名片辨識服務 — 整合指南

> 本文件的讀者是**要把這個服務架進自家後端的工程師**。
> 看完可以獨立完成部署與串接，不需要讀專案的其他文件。

上傳一張名片照片，回傳結構化的聯絡資訊（公司、姓名、職稱、電話、Email、地址等 10 個欄位）。
辨識由 OpenAI `gpt-5.6-luna` 完成，**不需要 GPU**，一台普通的 CPU 機器即可。

---

## 目錄

1. [你需要準備什麼](#1-你需要準備什麼)
2. [安裝與啟動](#2-安裝與啟動)
3. [設定](#3-設定)
4. [⚠️ 安全性：務必先讀](#4-️-安全性務必先讀)
5. [⚠️ 隱私與法遵：務必先讀](#5-️-隱私與法遵務必先讀)
6. [API 契約](#6-api-契約)
7. [串接注意事項](#7-串接注意事項)
8. [呼叫範例](#8-呼叫範例)
9. [成本](#9-成本)
10. [維運](#10-維運)
11. [已知限制](#11-已知限制)
12. [疑難排解](#12-疑難排解)

---

## 1. 你需要準備什麼

| 項目 | 說明 |
|---|---|
| **OpenAI API key** | **貴司自己的金鑰**，費用計在貴司帳上。需要能取用 `gpt-5.6-luna` |
| 一台機器 | 普通 CPU 即可，2 vCPU / 2GB RAM 綽綽有餘。**不需要 GPU** |
| Python 3.10+ 或 Docker | 兩種安裝方式擇一 |
| 對外網路 | 需連得到 `api.openai.com`（443） |

**不需要**：GPU、CUDA、MinerU、Ollama、vLLM，或任何本機模型。
專案裡另有一條需要 GPU 的本機管線（`app/`），**整合時請忽略它**，本文件只涉及 `cloud/`。

---

## 2. 安裝與啟動

### 方式 A：Docker（建議）

```bash
# 在專案根目錄
cp .env.cloud.example .env.cloud
# 編輯 .env.cloud，填入 OPENAI_API_KEY

docker compose -f deploy/cloud/docker-compose.yml up -d --build
curl http://localhost:8100/health
```

映像約 250MB。資料（上傳的原圖 + SQLite）落在 `deploy/cloud/data/`，
已設為 volume，容器重建不會遺失。

### 方式 B：直接跑

```bash
python3 -m venv .venv_cloud                      # 或 uv venv --python 3.12 .venv_cloud
.venv_cloud/bin/pip install -r deploy/cloud/requirements.txt

cp .env.cloud.example .env.cloud
# 編輯 .env.cloud，填入 OPENAI_API_KEY

bash start.cloud.sh                              # → http://0.0.0.0:8100
```

### 確認可用

```bash
curl http://localhost:8100/health
# {"status":"ok","db":"ok","pipeline":"cloud-vision","model":"gpt-5.6-luna",
#  "provider_configured":true,"heic_supported":true}

curl -X POST http://localhost:8100/api/cards/upload -F "file=@test/test_card1.png"
```

`provider_configured` 為 `false` 代表金鑰沒讀到，上傳會回 503。

互動式 API 文件在 `http://localhost:8100/docs`（Swagger UI）。

---

## 3. 設定

全部設定都在 `.env.cloud`。完整清單見 `.env.cloud.example`，實務上會動到的只有這幾個：

| 變數 | 預設 | 說明 |
|---|---|---|
| `OPENAI_API_KEY` | *(必填)* | 貴司的金鑰。**不要 commit 進版控**——`.gitignore` 已排除 `.env.cloud` |
| `CLOUD_HOST` | `0.0.0.0` | 綁定位址。**若不需對外，改成 `127.0.0.1`**（見第 4 節） |
| `CLOUD_PORT` | `8100` | 服務埠 |
| `DATABASE_URL` | `sqlite:///./card_ocr.db` | 預設 SQLite。要用 PostgreSQL 見第 11 節 |
| `MEDIA_DIR` | `media` | 上傳原圖的存放目錄 |
| `CLOUD_IMAGE_RETENTION_DAYS` | `0` | `0` = 永久保留。**建議依貴司的個資保留政策設定**（見第 5 節） |
| `CLOUD_MAX_UPLOAD_BYTES` | `20971520` | 20 MiB 上限，超過回 413 |

**不建議調整的兩個**（已實測驗證，見 `benchmarks.md`）：

- `CLOUD_REASONING_EFFORT=low` — 調成 `none` 可省 20%、快一倍，但實測會把字形相近的
  漢字讀錯（「高都」→「京都」，3 次錯 2 次）並漏抓多支號碼中的一支。
- `CLOUD_IMAGE_MAX_EDGE=2048` — 送出前的縮圖上限。手機拍照時名片常只佔畫面 50–70%，
  2048 才能讓 6pt 的地址小字保有約 14px 字高。調低會影響小字辨識。

---

## 4. ⚠️ 安全性：務必先讀

**這個服務本身沒有任何認證機制。**

沒有 API key、沒有 token 驗證，CORS 是 `allow_origins=["*"]`。
只要能連到該埠的人，都可以：

- 上傳圖片，**消耗貴司的 OpenAI 額度**（無速率限制）
- `GET /api/cards/{id}` 逐一遞增 ID，**讀取資料庫中所有名片的姓名、電話、Email、地址**
- `PUT /api/cards/{id}` 竄改任何一筆資料

這是刻意的設計——它預期是**內部服務**，由貴司的後端呼叫，認證與授權由貴司既有的機制處理。

**部署時務必至少做到其中一項：**

| 做法 | 說明 |
|---|---|
| **綁 localhost**（最簡單） | `CLOUD_HOST=127.0.0.1`，只有同一台機器的程式連得到 |
| **內網 + 防火牆** | 只開放給貴司後端的 IP |
| **反向代理 + 認證** | Nginx / API Gateway 上加 API key 或 mTLS |
| **Docker 網路隔離** | compose 裡拿掉 `ports:`，改用 service name 讓同網路的容器互連 |

**絕對不要**把 `8100` 直接暴露在公網。

> 若貴司需要服務內建 API key 驗證，可提出——大約 40 行 middleware 即可，
> 但預設不做，避免與貴司既有的認證機制重複。

---

## 5. ⚠️ 隱私與法遵：務必先讀

**名片影像會傳送到 OpenAI 進行辨識。**

名片包含姓名、電話、Email、地址等個人資料，因此這牽涉個資的**跨境傳輸與委外處理**。

服務端已內建的措施：

| 措施 | 說明 |
|---|---|
| `store=false` | 請求不保留在 OpenAI 端（可用 `CLOUD_STORE_RESPONSES` 調整，**不建議改**） |
| 只送縮圖 | 全解析度原圖留在本機 `media/`，送出的是長邊 2048 的 JPEG 副本 |
| log 不含個資 | 只記錄檔名、耗時、token 用量；不記 `raw_text`、Email、電話、地址 |
| 保留期自動清除 | `CLOUD_IMAGE_RETENTION_DAYS` 到期自動刪除原圖 |

**貴司需要自行處理的：**

1. 在隱私政策中揭露「名片影像會傳送至第三方（OpenAI）進行辨識」
2. 確認《個人資料保護法》的**跨境傳輸告知義務**是否適用於貴司的情境
3. 若名片來自貴司客戶，確認蒐集與利用的告知同意範圍涵蓋此處理
4. 對照 OpenAI **當前**的資料保留與訓練條款（本文件不代為斷言其內容，請以官方條文為準）
5. 設定 `CLOUD_IMAGE_RETENTION_DAYS` 以符合貴司的資料保留政策

> 若貴司的合規要求不允許個資離開自有環境，專案裡另有一條**完全本機**的管線（`app/`），
> 名片個資不離開機器——但需要 GPU，單張約 5 秒（L4）。詳見專案根目錄的 `README.md`。

---

## 6. API 契約

Base URL：`http://<host>:8100`

### `POST /api/cards/upload`

上傳名片照片，回傳結構化欄位。**這是唯一會產生費用的端點。**

**Request** — `multipart/form-data`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `file` | file | 名片圖片。支援 `image/jpeg`、`image/png`、`image/webp`；安裝了 `pillow-heif` 時另支援 `image/heic`、`image/heif`（iPhone 原生格式）。上限 20 MiB |

**Response 200** — `application/json`

```json
{
  "id": 1,
  "image_path": "media/cloud/f4663742-2737-496f-90f4-84059c12e1c2.png",
  "company_name": "有巢氏房屋",
  "person_name": "賴盈榕",
  "english_name": null,
  "job_title": "經紀人",
  "email": null,
  "phone": "（04）2326-2888",
  "mobile": "0986-876682",
  "fax": "（04）2326-2788",
  "address": "台中市南屯區大業路296號",
  "website": null,
  "raw_text": "有巢氏房屋\n賴盈榕\n電話 （04）2326-2888\n...",
  "ocr_confidence": 1.0,
  "created_at": "2026-09-04T19:16:40"
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | int | 資料庫主鍵，後續查詢/更新用 |
| `image_path` | string | 原圖相對路徑，可透過 `GET /media/...` 取得 |
| 10 個解析欄位 | string \| **null** | 見下表。**任何一個都可能是 `null`** |
| `raw_text` | string | 模型讀到的完整原始文字（逐行轉錄） |
| `ocr_confidence` | float | **固定為 `1.0`**，僅為相容保留，不代表實際信心度 |
| `created_at` | string | ISO 8601 時間戳 |

**10 個解析欄位：**

| 欄位 | 說明 |
|---|---|
| `company_name` | 公司名稱 |
| `person_name` | 姓名（卡片主要語言） |
| `english_name` | 英文姓名（中文名片上另標的英文名） |
| `job_title` | 職稱 |
| `email` | 電子郵件 |
| `phone` | 市話 / 公司電話 |
| `mobile` | 手機 |
| `fax` | 傳真 |
| `address` | 地址 |
| `website` | 網址 |

**Response headers**

| Header | 說明 |
|---|---|
| `X-Card-Validation-Issues` | *(選擇性)* 逗號分隔的欄位名。存在時代表這些欄位沒通過內部一致性檢查，**但資料仍已儲存並回傳**。建議在貴司 UI 上標記，提示使用者確認 |

**錯誤狀態碼**

| 狀態 | 意義 | 該不該重試 |
|---|---|---|
| `400` | 檔案格式不支援，或圖片無法解碼 | ❌ 不要重試 |
| `413` | 檔案超過 `CLOUD_MAX_UPLOAD_BYTES` | ❌ 請先壓縮 |
| `422` | 模型拒絕處理該圖／輸出被截斷／回傳不符 schema | ❌ 通常換張圖才有用 |
| `502` | OpenAI 認證失敗或伺服器錯誤 | ⚠️ 有限度重試；持續發生請查金鑰 |
| `503` | 金鑰未設定／連不上 OpenAI／被 rate limit（附 `Retry-After`） | ✅ 依 `Retry-After` 重試 |
| `504` | OpenAI 逾時 | ✅ 可重試 |

錯誤格式：`{"detail": "..."}`

### `GET /api/cards/{id}`

查詢已處理的名片。回傳格式同上。找不到回 `404`。**不產生費用。**

### `PUT /api/cards/{id}`

修正解析欄位（供人工校正 UI 使用）。**不產生費用。**

```json
{ "person_name": "賴盈榕", "job_title": "資深經紀人" }
```

只更新有傳入的欄位，未傳入的維持原值。回傳更新後的完整物件。找不到回 `404`。

### `GET /health`

健康檢查。**不會呼叫 OpenAI、不產生費用**，可安全地接進監控（建議 30 秒一次）。

```json
{"status":"ok","db":"ok","pipeline":"cloud-vision","model":"gpt-5.6-luna",
 "provider_configured":true,"heic_supported":true}
```

資料庫異常回 `503`。

### `GET /health/provider`

確認 OpenAI 金鑰有效且模型可取用。不耗 token，但**請勿接進監控或 Docker HEALTHCHECK**
（會對 OpenAI 產生不必要的請求）。僅供人工排查。

### `GET /media/{path}`

取得已上傳的原圖。**URL 就是 `image_path` 前面加一個斜線**：

```
image_path = "media/cloud/fb78c156-....png"
URL        = "/media/cloud/fb78c156-....png"      # 即 "/" + image_path
```

回傳全解析度原圖（不是送去辨識的縮圖）。不產生費用。

> 這個路由**同樣沒有認證**——見第 4 節。若名片影像不該對外，
> 請確保這個埠沒有暴露，或在反向代理層擋掉 `/media`。

---

## 7. 串接注意事項

### ⏱ 逾時設定（最容易踩的坑）

**單張辨識需要 3–15 秒**（實測平均 6 秒，複雜版面可到 15 秒）。

多數 HTTP client 的預設逾時遠低於此：

| Client | 預設 | 建議 |
|---|---|---|
| Python `requests` | 無限（但常被設成 5–10s） | `timeout=60` |
| Python `httpx` | 5 秒 ❌ | `timeout=60.0` |
| Node `axios` | 無限 | `timeout: 60000` |
| Node `fetch` | 依實作 | `AbortSignal.timeout(60000)` |
| Java `HttpClient` | 無限 | `.timeout(Duration.ofSeconds(60))` |
| Nginx `proxy_read_timeout` | **60 秒** | 若前面有反向代理，確認 ≥ 90 秒 |

**建議設 60 秒**。服務端本身對 OpenAI 的逾時是 60 秒（`CLOUD_OPENAI_TIMEOUT`），
加上前後處理，90 秒是安全值。

### 🔁 重試

服務端已對 OpenAI 做了 3 次指數退避重試，貴司端不需要重複做細粒度重試。

只需處理：
- `503` / `504` → 可重試，若有 `Retry-After` 請遵守
- `502` → 有限度重試（最多 1–2 次）
- `400` / `413` / `422` → **不要重試**，重試不會成功

### 📞 電話號碼格式：不做正規化

**回傳的是名片上印刷的原樣字串**，包含全形括號、各種分隔符：

```
（04）2326-2888        ← 全形括號，台灣名片常見
(02) 8666-8800
0986-876682
+886-4-2326-2888
```

這是刻意設計——保留原樣才能與名片對照、也才能讓內部驗證機制運作。
**若貴司需要 E.164 或其他統一格式，請在自己這端做正規化。**

### 🈳 所有欄位都可能是 null

名片本來就常沒有 Email、網址、英文名。`null` 代表「卡片上沒有」或「無法辨識」，
兩者不做區分。請勿假設任何欄位必定有值。

### 🔀 併發

服務端以 `--workers 1` 啟動，請求會排隊。100–200 張/天 的量完全不成問題。
若需要更高吞吐，可調高 worker 數（無模型佔用記憶體，不像 GPU 版有此限制）；
但請注意 SQLite 的寫入鎖，量大時建議改用 PostgreSQL（見第 11 節）。

### 📐 圖片建議

- **手機直拍即可**，服務端會自動處理 EXIF 旋轉、縮圖、去背景透明
- 名片盡量填滿取景框——名片佔畫面比例越高，小字（地址、統編）越準
- 不需要事先壓縮或轉檔；HEIC 也可直接上傳

---

## 8. 呼叫範例

### curl

```bash
curl -X POST http://localhost:8100/api/cards/upload \
  -F "file=@card.jpg" \
  --max-time 90
```

### Python

```python
import requests

def scan_card(path: str, base_url: str = "http://localhost:8100") -> dict:
    with open(path, "rb") as f:
        r = requests.post(
            f"{base_url}/api/cards/upload",
            files={"file": (path, f, "image/jpeg")},
            timeout=90,                      # 單張需 3-15 秒，務必放寬
        )

    if r.status_code in (503, 504):          # 可重試
        raise RetryableError(r.json().get("detail"), retry_after=r.headers.get("Retry-After"))
    if r.status_code != 200:                 # 400/413/422 不要重試
        raise PermanentError(r.status_code, r.json().get("detail"))

    card = r.json()
    if issues := r.headers.get("X-Card-Validation-Issues"):
        # 這些欄位沒通過內部檢查,但資料仍然可用——建議在 UI 上標記提示使用者確認
        card["_needs_review"] = issues.split(",")
    return card
```

### Node.js

```javascript
import { readFile } from "node:fs/promises";

async function scanCard(path, baseUrl = "http://localhost:8100") {
  const form = new FormData();
  form.append("file", new Blob([await readFile(path)]), "card.jpg");

  const res = await fetch(`${baseUrl}/api/cards/upload`, {
    method: "POST",
    body: form,
    signal: AbortSignal.timeout(90_000),     // 單張需 3-15 秒
  });

  if (!res.ok) {
    const { detail } = await res.json().catch(() => ({}));
    const retryable = [503, 504].includes(res.status);
    throw Object.assign(new Error(detail ?? res.statusText), { status: res.status, retryable });
  }

  const card = await res.json();
  const issues = res.headers.get("X-Card-Validation-Issues");
  if (issues) card._needsReview = issues.split(",");
  return card;
}
```

---

## 9. 成本

費用直接計在**貴司的 OpenAI 帳號**上，沒有其他授權或訂閱費用。

| 情境 | 每張 | 100 張/天 | 200 張/天 |
|---|---:|---:|---:|
| 掃描圖 / 小尺寸照片 | 約 US$0.0009 | 約 US$2.6/月 | 約 US$5.2/月 |
| 手機實拍（縮到 2048px） | 約 US$0.0016 | 約 US$4.7/月 | 約 US$9.3/月 |

只有 `POST /api/cards/upload` 會產生費用；`GET` / `PUT` / `/health` 都不會。

實測數據與量測方法見 `deploy/benchmarks.md`。

---

## 10. 維運

### 監控

`GET /health` 每 30 秒一次即可（不耗費用）。
`status` 非 `ok` 或 `provider_configured` 為 `false` 時告警。

### 日誌

每次辨識會輸出一行，可用來核對用量與成本：

```
Vision extract done: model=gpt-5.6-luna effort=low in=2280 cached=1405 out=452 reasoning=254 time=6.78s
```

`cached` 是命中 prompt cache 的 token 數（費率較低）。
**日誌不含任何個資**——不會記錄 `raw_text`、Email、電話或地址。

### 資料

| 項目 | 位置 | 備註 |
|---|---|---|
| 上傳原圖 | `media/cloud/`（Docker：`deploy/cloud/data/media/`） | 全解析度，約 0.7MB/張 |
| 辨識結果 | SQLite `card_ocr.db`（Docker：`deploy/cloud/data/`） | |

**備份**：直接備份這兩個路徑即可。**內含個資，請依貴司政策加密與控管存取。**

**磁碟成長**：200 張/天 × 0.7MB ≈ 每年 51GB。
請設定 `CLOUD_IMAGE_RETENTION_DAYS`，服務會每 24 小時自動清除過期原圖
（只清 `media/cloud/`，不影響其他目錄）。

---

## 11. 已知限制

| 限制 | 說明 |
|---|---|
| **無認證** | 見第 4 節。必須由貴司的 gateway 或網路隔離處理 |
| **OpenAI 中斷 = 服務中斷** | 辨識完全依賴外部 API，沒有本機備援。若無法接受，考慮改用本機 GPU 管線 |
| **`ocr_confidence` 無意義** | 固定為 `1.0`，僅為相容保留 |
| **不做繁簡轉換** | 卡片印簡體就回傳簡體，照原樣保真 |
| **雙面名片** | 需分別上傳；服務不會自動合併兩面 |
| **一卡多值** | 若名片有兩個 Email 或多支電話，可能被塞進同一個欄位。`raw_text` 保有完整資訊 |
| **SQLite 併發** | 預設 SQLite 適合 100–200 張/天。量大或需多實例時，把 `DATABASE_URL` 改成 PostgreSQL——但需同時移除 `app/database.py` 中 `connect_args={"check_same_thread": False}`（該參數僅 SQLite 適用） |

**建議前端提供欄位校正 UI**，並用 `PUT /api/cards/{id}` 回寫。
辨識準確度高但非 100%，尤其職稱、公司名的斷詞與雙語名片的姓名歸位。

---

## 12. 疑難排解

**`{"detail": "vision provider not configured (OPENAI_API_KEY unset)"}` (503)**
`.env.cloud` 的 `OPENAI_API_KEY` 沒填或沒被讀到。確認檔案位置在專案根目錄、
且用的是 `.env.cloud` 而非 `.env.cloud.example`。用 `curl /health` 看 `provider_configured`。

**`APIConnectionError` / 503，但金鑰是對的**
檢查 `.env.cloud` 裡的 `OPENAI_BASE_URL` ——若不使用 proxy／gateway，**請整行刪除或註解掉**，
不要留成 `OPENAI_BASE_URL=`。（服務端已會清除空值，但舊版設定檔仍可能有此問題。）
另確認機器連得到 `api.openai.com:443`。

**client 端逾時，但 server log 顯示成功**
Client 的逾時設太短。見第 7 節，建議 60–90 秒。若中間有 Nginx，
記得調 `proxy_read_timeout`（預設 60 秒）。

**iPhone 上傳回 400**
HEIC 需要 `pillow-heif`。用 `curl /health` 看 `heic_supported` 是否為 `true`；
為 `false` 時執行 `pip install pillow-heif` 後重啟。

**名片上的小字（地址、統編）辨識不全**
提高 `CLOUD_IMAGE_MAX_EDGE`（例如 `2560`），或引導使用者讓名片填滿取景框。

**照片方向不對 / 名片是躺著的**
服務端已自動處理 EXIF 旋轉。若仍發生，代表該照片沒有 EXIF orientation tag，
需在拍攝端修正。

---

## 相關文件

| 文件 | 內容 |
|---|---|
| `deploy/cloud/README.md` | 部署細節、完整環境變數表、成本分析 |
| `deploy/benchmarks.md` | 實測數據：延遲、成本、reasoning effort A/B |
| `.env.cloud.example` | 所有設定項的完整註解 |
| 根目錄 `README.md` | 專案總覽、以及需要 GPU 的本機管線 |
