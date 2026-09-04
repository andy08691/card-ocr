# Business Card OCR API

接收名片圖片，自動識別並回傳結構化聯絡資訊的後台 API。

架構分兩層：**MinerU（OCR，把圖片轉成文字＋版面座標）** → **欄位擷取（把文字歸到 10 個欄位）**。
欄位擷取預設走「**candidate（regex 找候選）→ 本地 LLM 分類 → validator 驗證 → 最多 1 次 repair**」，
LLM 跑在本機 Ollama，**名片個資（姓名 / 電話 / Email / 地址）完全不離開本機**；LLM 失敗時自動
退回純 regex parser。可用 `CARD_EXTRACTOR=regex` 一鍵切回舊版純 regex。

## 功能

- 上傳名片圖片（JPG / PNG / WebP）
- 使用 **MinerU**（OpenDataLab）VLM 模型識別文字（支援中文、英文名片，自動偵測語言）
- 大尺寸圖片自動縮圖後再識別（保留清晰度、降低記憶體）
- **本地 LLM 欄位擷取**：regex 找候選、Ollama 本地 LLM 依標籤/版面分類（跨中英日韓混排更穩），
  validator 嚴格驗證候選型欄位、最多 repair 1 次，失敗自動退回 regex（詳見「欄位擷取流程」）
- 解析欄位：公司名、姓名、英文名、職稱、Email、市話、手機、傳真、地址、網站
- 從 Email 推斷欄位：local part → 姓名（`ian.christian` → Ian Christian）、domain → 公司名（`sprayway.com` → Sprayway）
- OCR 合字自動修正（`OSCLimited` → `OSC Limited`）
- 儲存原圖與解析結果至本機
- 提供修正 API，供前端使用者手動修改欄位
- 可掛雲端 / 自建 GPU 部署，單張約 5–10 秒（見 `deploy/`）

---

## 環境需求

- OCR 引擎：**MinerU 3.x**（OpenDataLab）
- 欄位擷取 LLM：**Ollama**（本機 server）+ 模型 `qwen3:4b`（約 2.5GB；`CARD_EXTRACTOR=regex` 時不需要）
- Python 3.10–3.13
- 記憶體建議 ≥ 16GB、可用磁碟 ≥ 20GB（MinerU + LLM 模型合計約數 GB）
- 硬體加速（MinerU 自動偵測，無需設定）：
  - macOS Apple Silicon → **MLX**（本專案主要測試環境）
  - Linux + NVIDIA GPU → **vLLM**（速度關鍵；需釘 `vllm==0.10.2`，見 `deploy/`）
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

（`requirements.txt` 已含 `ollama` python client——僅是透過 HTTP 連本機 Ollama server 的輕量套件。）

### 5. 安裝本地 LLM（欄位擷取層）

預設 `CARD_EXTRACTOR=llm` 會用本機 Ollama 做欄位分類，需先安裝 Ollama 並拉模型：

```bash
# 安裝 Ollama：見 https://ollama.com/download（macOS 有 App；Linux 用官方 install.sh）
ollama pull qwen3:4b        # 首次下載約 2.5GB
# ollama serve 通常已在背景執行；app 只透過 http://localhost:11434 連它
```

> 不想用 LLM？在 `.env` 設 `CARD_EXTRACTOR=regex` 即可一鍵切回純 regex parser，
> 這樣就不需要 Ollama / 模型（精度較低、但零額外依賴）。

### 啟動 Server（開發模式）

```bash
.venv_mineru/bin/uvicorn app.main:app --reload
```

啟動後 server 會在**背景**預先載入 MinerU 模型（warmup），接著預熱 LLM（若 `CARD_EXTRACTOR=llm`），
此時 `/health` 已可回應；待模型載入完成後，第一個上傳請求即無需再等待載入。

> 效能提醒：
> - **Mac（MLX，開發用）**：每張約 10–30 秒（MinerU + LLM，首張含載入較久）。
> - **GPU（vLLM，見 `deploy/`）**：實測 L4 約 **~5 秒/張**（暖機後）。
>
> 呼叫端請設定足夠的逾時時間。

---

## 對外部署（供其他 Server 使用）

此 OCR server 可直接在其他公司的機器上執行，讓他們的後端程式透過 HTTP 呼叫 API。

### 設定

複製 `.env.example` 為 `.env`：

```bash
cp .env.example .env   # macOS / Linux
copy .env.example .env  # Windows
```

主要環境變數（完整見 `.env.example`）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `HOST` | `0.0.0.0` | 綁定位址；`0.0.0.0` 讓同機/同網路連得到 |
| `PORT` | `8000` | 服務埠，被佔用時可改（如 `8080`）|
| `CARD_EXTRACTOR` | `llm` | `llm`＝candidate+本地 LLM 分類；`regex`＝一鍵回退純 regex |
| `MINERU_BACKEND` | `vlm-engine` | 留空＝自動選（Mac→MLX、Linux+vllm→vLLM、否則 transformers）；亦可指定 `pipeline` 等 |
| `MINERU_PDF_RENDER_THREADS` | `1` | 名片單頁，1 即可，勿調高 |
| `OLLAMA_MODEL` | `qwen3:4b` | 欄位分類模型（換模型改這裡）|
| `OLLAMA_HOST` | `http://localhost:11434` | 本機 Ollama server 位址 |
| `OLLAMA_KEEP_ALIVE` | `30m` | 模型常駐時間；長＝後續請求更快、短＝閒置早釋放 RAM |

> GPU 部署時另有 `MINERU_VIRTUAL_VRAM_SIZE`（限 vLLM VRAM、留給 Ollama 共存）等，見 `deploy/README.md`。

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

### GPU 雲端 / 自建部署（~5 秒/張）

要達到單張 5–10 秒，需在 GPU 上用 MinerU 的 **vLLM** 後端。完整部署包在 `deploy/`：

- `deploy/Dockerfile` + `deploy/entrypoint.sh` — 通用 GPU 映像（RunPod / Cloud Run / Fly / 自建），
  一個容器同時跑 Ollama(qwen3:4b) + FastAPI。
- `deploy/lightning_setup.sh` / `lightning_run.sh` / `lightning.md` — **Lightning AI Studio**（免 Docker、
  原樣跑，推薦用來驗證速度）。
- `deploy/beam_app.py` — Beam Pod 定義。
- `deploy/README.md` — 各平台步驟、環境變數表、GPU/VRAM 共存說明。

> ⚠️ **vLLM 必須釘 `0.10.2`**：0.28+ 會讓 MinerU 的 no-repeat-ngram logits processor 失效、輸出重複亂碼；
> 且用 `uv` 安裝（pip 對 vLLM 依賴會 backtracking 卡死）。部署腳本已處理好，細節見 `deploy/README.md`。

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

## 欄位擷取流程（`CARD_EXTRACTOR=llm`，預設）

MinerU 只負責 OCR（文字＋版面座標）；把文字歸到 10 個欄位的工作交給下列管線，
把「找資訊」（regex 可靠、確定性）與「判斷欄位」（LLM 擅長、跨語言）分工：

```
run_ocr()  →  {raw_text, boxes, lang}                     # ocr.py（OCR，不做分類）
        ↓
extract_card(raw_text, boxes, lang)                        # card_extractor.py（workflow）
  1. candidates = extract_candidates(raw_text)             # candidate_extractor.py：regex 只找 email/phone/website 候選（不分類）
  2. result     = await llm.parse(...)                     # llm_parser.py：本機 Ollama structured output → 10 欄位
  3. issues     = validate_card(result, candidates)        # validator.py：候選型欄位嚴格 membership + 衝突檢查
  4. if issues: result = await llm.repair(...)             # 針對 issues 修正，最多 1 次
  5. snap 候選型欄位到確定性候選值 → 回傳 10-key dict
        ↓  （Ollama/LLM 任何錯誤 → 自動 fallback 到 regex parse_card，不回 422）
Card(**parsed)  →  SQLite
```

設計重點：

- **regex `parser.py` 完整保留**——同時是 baseline、LLM 失敗的 fallback，也是既有測試的對象。
- **validator 分兩類**：候選型（`email/phone/mobile/fax/website`）做嚴格值比對＋衝突檢查；
  模糊型（`company_name/person_name/english_name/job_title/address`）只做軟檢查，避免把 LLM 修對的又改壞。
- **優雅退場**：repair 上限 1 次，仍有問題就存 best-effort 並 `logger.warning`，OCR 成功不因分類沒過而整張失敗。
- **一鍵回退**：`CARD_EXTRACTOR=regex` 直接走純 regex，不需 Ollama。

---

## 專案結構

```
card_ocr/
├── app/
│   ├── main.py          # FastAPI 入口，CORS、靜態檔案、路由、/health、背景 warmup（MinerU + LLM）
│   ├── database.py      # SQLite 設定（SQLAlchemy）
│   ├── models/
│   │   └── card.py      # 資料庫 ORM model（含 fax 欄位）
│   ├── schemas/
│   │   ├── card.py       # Pydantic 請求/回應 schema
│   │   └── extraction.py # CardExtraction（10 欄位）、CardCandidates（候選）schema
│   ├── routers/
│   │   └── cards.py     # API 路由（upload / get / update）
│   └── services/
│       ├── ocr.py                 # MinerU 封裝（中英自動偵測、自動縮圖、簡轉繁）
│       ├── candidate_extractor.py # regex 只找 email/phone/website 候選（重用 parser 的 regex）
│       ├── llm_parser.py          # LocalLLMParser：Ollama structured output（parse / repair / warmup）
│       ├── validator.py           # validate_card：候選型嚴格驗、模糊型軟檢查
│       ├── card_extractor.py      # extract_card：candidate→LLM→validate→repair→fallback 的 workflow
│       └── parser.py              # Regex + 規則解析欄位（baseline / LLM fallback，不改動）
├── tests/
│   ├── test_parser.py             # parser 單元測試（無需啟動 server）
│   ├── test_ocr_adapter.py        # MinerU content_list → boxes adapter 測試（無需模型）
│   ├── test_candidate_extractor.py # 候選擷取：聯集去重（離線）
│   ├── test_validator.py          # validator 各規則（離線）
│   └── test_llm_extract.py        # LLM 契約 / prompt（mock Ollama，離線）
├── deploy/              # GPU 部署包（Dockerfile / Lightning / Beam；見 deploy/README.md）
├── media/               # 上傳的名片圖片（git 忽略）
├── card_ocr.db          # SQLite 資料庫（git 忽略）
├── .venv_mineru/        # MinerU 虛擬環境（git 忽略）
├── requirements.txt         # 應用層依賴（含 ollama client；OCR 引擎 MinerU 另行安裝，見安裝步驟）
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

**全部離線，不需啟動 server、不需載入 MinerU 模型、不需 Ollama server：**

- `test_parser.py` — parser 欄位邏輯（baseline）
- `test_ocr_adapter.py` — MinerU 輸出轉 boxes 的 adapter（餵假的 content_list）
- `test_candidate_extractor.py` — 候選擷取的聯集去重
- `test_validator.py` — validator 各規則（membership / 衝突 / 軟檢查）
- `test_llm_extract.py` — LLM 契約與 prompt（mock 掉 Ollama 呼叫）

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
- 速度依硬體差異大：Mac(MLX) 約 10–30 秒/張、GPU(vLLM) 約 5–10 秒/張，呼叫端請設定足夠逾時
- `ocr_confidence` 在 MinerU 下固定為 `1.0`（content_list 不提供 per-box 分數），此欄位僅為相容保留、不代表實際信心度
- MinerU 中文 OCR 可能輸出簡體，已用 OpenCC `s2twp` 轉繁；日文等非中文名片的漢字可能被過度轉換
- **LLM 分類受模型大小影響**：`qwen3:4b` 偶爾會漏分某些帶標籤的號碼（實測全形括號的市話
  `電話（04）…` 有時未歸到 `phone`）；validator 只驗值不強制補齊，此類分類缺口需靠更大模型或
  prompt 微調改善（Phase 2 評測另案）。急用可 `CARD_EXTRACTOR=regex` 回退，或前端提供欄位修正。
