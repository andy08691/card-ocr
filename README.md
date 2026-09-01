# Business Card OCR API

接收名片圖片，自動識別並回傳結構化聯絡資訊的後台 API。

## 功能

- 上傳名片圖片（JPG / PNG / WebP）
- 使用 **MinerU**（OpenDataLab）VLM 模型識別文字（支援中文、英文名片，自動偵測語言）
- 大尺寸圖片自動縮圖後再識別（保留清晰度、降低記憶體）
- 解析欄位：公司名、姓名、英文名、職稱、Email、市話、手機、傳真、地址、網站
- 從 Email 推斷欄位：local part → 姓名（`ian.christian` → Ian Christian）、domain → 公司名（`sprayway.com` → Sprayway）
- OCR 合字自動修正（`OSCLimited` → `OSC Limited`）
- 儲存原圖與解析結果至本機
- 提供修正 API，供前端使用者手動修改欄位

---

## 環境需求

- OCR 引擎：**MinerU 3.x**（OpenDataLab）
- Python 3.10–3.13
- 記憶體建議 ≥ 16GB、可用磁碟 ≥ 20GB（模型約數 GB）
- 硬體加速（MinerU 自動偵測，無需設定）：
  - macOS Apple Silicon → **MLX**（本專案主要測試環境）
  - Linux + NVIDIA GPU → **vllm**
  - 其餘 → CPU（transformers，較慢）
- 建議使用 [`uv`](https://github.com/astral-sh/uv) 管理套件（安裝快、相依解析穩定）

---

## 安裝步驟

### 1. 建立虛擬環境（Python 3.10–3.13）

```bash
uv venv --python 3.11 .venv_mineru
```

### 2. 安裝 MinerU（依平台選一）

```bash
# macOS Apple Silicon（MLX 加速，推薦）
VIRTUAL_ENV=.venv_mineru uv pip install -U "mineru[mlx]"

# Linux + NVIDIA GPU
VIRTUAL_ENV=.venv_mineru uv pip install -U "mineru[vllm]"   # 或 "mineru[all]"

# 純 CPU
VIRTUAL_ENV=.venv_mineru uv pip install -U "mineru[core]"
```

### 3. 下載模型（首次，約數 GB）

```bash
.venv_mineru/bin/mineru-models-download -s huggingface -m vlm
```

> 從中國大陸連線可改用 `-s modelscope`。下載完成會在 `~/mineru.json` 寫入模型路徑設定。

### 4. 安裝應用層依賴

```bash
VIRTUAL_ENV=.venv_mineru uv pip install -r requirements.txt
```

### 啟動 Server（開發模式）

```bash
.venv_mineru/bin/uvicorn app.main:app --reload
```

啟動後 server 會在**背景**預先載入 MinerU 模型（warmup），此時 `/health` 已可回應；
待模型載入完成（約 10–20 秒）後，第一個上傳請求即無需再等待載入。

> 效能提醒：MinerU VLM 每張名片約需 10–40 秒（首張含模型載入較久），
> 明顯慢於舊版 PaddleOCR 的數秒，但擷取精度較高。呼叫端請設定足夠的逾時時間。

---

## 對外部署（供其他 Server 使用）

此 OCR server 可直接在其他公司的機器上執行，讓他們的後端程式透過 HTTP 呼叫 API。

### 設定

複製 `.env.example` 為 `.env`：

```bash
cp .env.example .env   # macOS / Linux
copy .env.example .env  # Windows
```

預設設定（可依需求修改）：

```
HOST=0.0.0.0   # 0.0.0.0 讓同機器或同網路的其他程式連得到
PORT=8000      # 如果 8000 已被佔用，改成其他 port（例如 8080）
```

### 啟動

**macOS / Linux：**
```bash
bash start.sh
```

**Windows：**
```cmd
start.bat
```

啟動後會顯示：`Starting OCR server on http://0.0.0.0:8000`

### 關閉

**macOS / Linux：**
```bash
bash stop.sh
```

**Windows：** 直接關閉終端機視窗，或按 `Ctrl+C`

---

## API 使用方式

啟動後 Swagger UI：`http://localhost:8000/docs`（可換成實際機器 IP 或自訂 PORT）

### GET `/health` — 健康檢查

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok", "db": "ok"}
```

### POST `/api/cards/upload` — 上傳名片

```bash
curl -X POST http://localhost:8000/api/cards/upload \
  -F "file=@your_card.jpg"
```

**回傳範例：**

```json
{
  "id": 1,
  "image_path": "media/xxxx.jpg",
  "company_name": "高都汽車股份有限公司",
  "person_name": "陳志均",
  "english_name": null,
  "job_title": "銷售顧問",
  "email": "AFDA914@toyota.com.tw",
  "phone": "07-7827271",
  "mobile": "0931034566",
  "fax": null,
  "address": "831高雄市大寮區力行路197號",
  "website": null,
  "raw_text": "TOYOTA\n高都汽車股份有限公司\n...",
  "ocr_confidence": 1.0,
  "created_at": "2026-04-04T01:39:33"
}
```

### GET `/api/cards/{id}` — 查詢名片

```bash
curl http://localhost:8000/api/cards/1
```

### PUT `/api/cards/{id}` — 修正欄位

供前端使用者修改 OCR 識別錯誤的欄位：

```bash
curl -X PUT http://localhost:8000/api/cards/1 \
  -H "Content-Type: application/json" \
  -d '{"person_name": "陳志均", "job_title": "銷售顧問"}'
```

---

## 解析欄位說明

| 欄位 | 說明 |
|---|---|
| `company_name` | 公司名稱（含中文後綴如「股份有限公司」或英文後綴如 Corp / Ltd / Oy 等） |
| `person_name` | 人名（中文 2–5 字 或 英文 Title Case / ALL CAPS） |
| `english_name` | 英文姓名（中文名片上的英文名） |
| `job_title` | 職稱（關鍵字比對） |
| `email` | 電子郵件 |
| `phone` | 市話 / 公司電話 |
| `mobile` | 手機號碼 |
| `fax` | 傳真號碼 |
| `address` | 地址 |
| `website` | 網址 |

### Email 推斷邏輯

當 OCR 無法識別出姓名或公司時，會從 Email 自動推斷：

- **姓名**：`ian.christian@sprayway.com` → local part `ian.christian` → `"Ian Christian"`
  - 各段至少 2 個字母，純英文字母，2–3 段才觸發
- **公司**：`sprayway.com` → domain 去掉 TLD → `"Sprayway"`
  - 個人信箱（gmail、yahoo、hotmail 等）不觸發此邏輯
  - 僅在所有其他方法都找不到公司名時才作為最後備援

---

## 專案結構

```
card_ocr/
├── app/
│   ├── main.py          # FastAPI 入口，CORS、靜態檔案、路由、/health
│   ├── database.py      # SQLite 設定（SQLAlchemy）
│   ├── models/
│   │   └── card.py      # 資料庫 ORM model（含 fax 欄位）
│   ├── schemas/
│   │   └── card.py      # Pydantic 請求/回應 schema
│   ├── routers/
│   │   └── cards.py     # API 路由（upload / get / update）
│   └── services/
│       ├── ocr.py       # MinerU 封裝（中英自動偵測、自動縮圖、簡轉繁）
│       └── parser.py    # Regex + 規則解析欄位
├── tests/
│   ├── test_parser.py       # parser 單元測試（無需啟動 server）
│   └── test_ocr_adapter.py  # MinerU content_list → boxes adapter 測試（無需模型）
├── media/               # 上傳的名片圖片（git 忽略）
├── card_ocr.db          # SQLite 資料庫（git 忽略）
├── .venv_mineru/        # MinerU 虛擬環境（git 忽略）
├── requirements.txt         # 應用層依賴（OCR 引擎 MinerU 另行安裝，見安裝步驟）
├── start.sh                 # 正式啟動腳本（macOS / Linux）
├── start.bat                # 正式啟動腳本（Windows）
├── stop.sh                  # 關閉 port 8000 的腳本（macOS / Linux）
├── .env.example             # 環境變數範本
└── .env                     # 環境設定（git 忽略）
```

---

## 執行測試

```bash
VIRTUAL_ENV=.venv_mineru uv pip install pytest   # 首次需安裝
.venv_mineru/bin/pytest tests/ -v
```

測試不需啟動 server 或載入 MinerU 模型：`test_parser.py` 測 parser 邏輯，
`test_ocr_adapter.py` 測 MinerU 輸出轉 boxes 的 adapter（餵入假的 content_list）。

---

## 資料庫欄位新增（升級現有安裝）

若從舊版升級，需手動新增 `fax` 欄位：

```bash
sqlite3 card_ocr.db "ALTER TABLE cards ADD COLUMN fax TEXT;"
```

---

## 常見錯誤與解法

### ❌ 找不到模型 / `mineru.json` 不存在

**原因：** 尚未下載 MinerU 模型。

**解法：**
```bash
.venv_mineru/bin/mineru-models-download -s huggingface -m vlm
```
下載完成會在 `~/mineru.json` 寫入模型路徑。

---

### ❌ `An attempt has been made to start a new process before ... bootstrapping`

**原因：** MinerU 以 spawn 模式的行程池做 PDF 轉圖，若在沒有 `if __name__ == '__main__'`
保護的頂層程式碼直接呼叫 `run_ocr`，spawn 子行程重新 import 時會遞迴触發。

**解法：** 用 `uvicorn app.main:app` 或 `pytest` 等正規入口啟動即可（其 `__main__` 在子行程會
被重新命名為 `__mp_main__`、不會重跑）。若要寫獨立腳本呼叫 `run_ocr`，請把呼叫放進
`if __name__ == "__main__":` 區塊內。

---

### ❌ 第一個請求很慢 / 記憶體吃緊

**原因：** MinerU VLM 需載入數 GB 模型，且在 16GB 機器上記憶體較緊。

**解法：**
- 模型會在啟動時於背景預熱（warmup），請等 log 出現 `MinerU warmup done` 再送請求。
- 保持 `--workers 1`（多 worker 會各自載入模型、記憶體翻倍）。
- 名片為單頁，`MINERU_PDF_RENDER_THREADS=1`（預設）即可，勿調高。
- 記憶體真的不足時，可用 `MINERU_BACKEND=pipeline` 改走較輕量的 CPU 管線。

---

### ❌ `table cards has no column named fax`

**原因：** 升級前已有舊版資料庫，缺少 `fax` 欄位。

**解法：**
```bash
sqlite3 card_ocr.db "ALTER TABLE cards ADD COLUMN fax TEXT;"
```

---

## 已知限制

- MinerU VLM 為文件版面模型，名片版面稀疏、多欄、含 logo，偶爾會合併或重排欄位；建議前端提供欄位修正功能
- 每張名片約需 10–40 秒（含首張模型載入），明顯慢於舊版 PaddleOCR，呼叫端請設定足夠逾時
- `ocr_confidence` 在 MinerU 下固定為 `1.0`（content_list 不提供 per-box 分數），此欄位僅為相容保留、不代表實際信心度
- MinerU 中文 OCR 可能輸出簡體，已用 OpenCC `s2twp` 轉繁；日文等非中文名片的漢字可能被過度轉換
