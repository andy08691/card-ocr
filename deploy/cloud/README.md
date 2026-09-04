# 雲端部署包（gpt-5.6-luna，無需 GPU）

把名片照片直接交給 **OpenAI `gpt-5.6-luna`**（vision + Structured Outputs）辨識，
不需要 MinerU、Ollama、vLLM，也不需要 GPU。一台普通 CPU VPS 就能跑。

與 `../Dockerfile`（GPU 版）互為替代——兩條管線共用同一份 10 欄位定義、
同一張 `cards` 資料表、同一個 `CardResponse`，前端只要換 base URL。

## 檔案

| 檔 | 用途 |
|---|---|
| `Dockerfile` | 純 CPU 映像（`python:3.12-slim`，約 250MB） |
| `requirements.txt` | 部署依賴（釘版本）；**不含** mineru / torch / opencc / ollama |
| `docker-compose.yml` | 一行起服務，含 volume 與 healthcheck |
| `../../.env.cloud.example` | 環境變數範本（複製成 `.env.cloud` 後填 API key） |
| `../../start.cloud.sh` | 不用 Docker、直接跑 uvicorn 的啟動腳本 |

---

## 管線

```
上傳照片 → prepare_image()  (EXIF 轉正 → 白底去 alpha → 長邊縮到 2048 → JPEG 品質階梯 → data URL)
        ↓                     ※ 原圖原封不動留在 media/cloud/
   VisionCardExtractor.extract()   # 一次呼叫同時做 OCR 與分類（strict structured output）
        ↓  → {raw_text, fields}
   extract_candidates(raw_text)    # 重用 app/：regex 只找候選、不分類
   validate_card(fields, ...)      # 重用 app/：候選型嚴格驗 + 衝突檢查
   （有 issue → repair 一次，連圖片一起重送讓模型重看；候選重算後再驗）
   _snap_candidates(...)           # 重用 app/：欄位 snap 回名片上印的表面字串
        ↓
   Card(**fields) → 同一張 cards 資料表 → 同一個 CardResponse
```

`raw_text` 由模型一併回傳，既是 API 回應欄位，也是第二關驗證的輸入。

---

## 快速開始

### A. Docker（推薦）

```bash
cp .env.cloud.example .env.cloud     # 在專案根目錄；填入 OPENAI_API_KEY
docker compose -f deploy/cloud/docker-compose.yml up -d --build

curl http://localhost:8100/health
curl -X POST http://localhost:8100/api/cards/upload -F "file=@test/test_card1.png"
```

原圖與 SQLite 落在 `deploy/cloud/data/`（volume）。

### B. 不用 Docker

```bash
uv venv --python 3.12 .venv_cloud
VIRTUAL_ENV=.venv_cloud uv pip install -r deploy/cloud/requirements.txt
cp .env.cloud.example .env.cloud     # 填入 OPENAI_API_KEY
bash start.cloud.sh                  # → http://0.0.0.0:8100
```

> `.venv_cloud` 可以跑 `pytest tests/test_cloud_*.py`，但**不能**跑整個 `tests/`
> ——`tests/test_ocr_adapter.py` 會 import `app.services.ocr` → `opencc`。
> 要一次跑全部，用已裝好 MinerU 的 `.venv_mineru`（`openai` 已在其中，只需補
> `uv pip install pillow-heif`）。

### 與本機管線並行

兩者可以同時跑：埠不同（8000 / 8100）、env 檔不同（`.env` / `.env.cloud`）、
圖片目錄不同（`media/` / `media/cloud/`）。
`stop.sh` 只匹配 `uvicorn app.main` 且只清 port 8000，不會誤殺雲端 server。

---

## 環境變數

完整清單見 `../../.env.cloud.example`。最常調的幾個：

| 變數 | 預設 | 說明 |
|---|---|---|
| `OPENAI_API_KEY` | *(必填)* | 未設時 server 仍會啟動、`/health` 仍答得出來，但 upload 回 503 |
| `CLOUD_PORT` | `8100` | 刻意不叫 `PORT`——`start.sh` / `entrypoint.sh` 已在用那個名字 |
| `CLOUD_VISION_MODEL` | `gpt-5.6-luna` | |
| `CLOUD_REASONING_EFFORT` | `low` | **成本的主導項**；`none` 再省約 3 成、`medium` 幾乎翻倍 |
| `CLOUD_IMAGE_MAX_EDGE` | `2048` | 為手機拍照調的；名片常只佔畫面 50–70%，2048 才讓 6pt 小字保有 ~14px |
| `CLOUD_STORE_RESPONSES` | `false` | 隱私：不留存在 provider 端 |
| `CLOUD_MAX_REPAIR` | `1` | 與 `app/services/card_extractor.py` 的 `_MAX_REPAIR` 對齊；`0` = 硬性成本上限 |
| `CLOUD_IMAGE_RETENTION_DAYS` | `0` | `0` 不刪；>0 時定期清理**只清 `media/cloud/`** |
| `DATABASE_URL` | `sqlite:///./card_ocr.db` | 設不同值即可與本機管線完全隔離 |

---

## 隱私

這條管線**會把名片影像送到 OpenAI**，與本機管線（個資不離開本機）的性質根本不同。
已內建的措施：

- **`store=false`**（`CLOUD_STORE_RESPONSES`）——請求不保留在 provider 端。
- **只送縮圖**——全解析度原圖留在 `media/cloud/`，送出的是長邊 2048 的 JPEG 副本。
- **PII 不進 log**——log 只記檔名、耗時、token 用量；不記 `raw_text`、email、電話、地址。
- **保留天數自動刪**——`CLOUD_IMAGE_RETENTION_DAYS`，只掃 `media/cloud/`，
  絕不碰本機管線在 `media/` 根目錄的圖。

> ⚠️ 上線前請自行對照 OpenAI **當前**的資料保留與訓練條款，並在你的隱私政策與
> 委外契約中揭露。台灣《個資法》的跨境傳輸告知義務需另行確認。本文件不代為斷言條款內容。

---

## 格式與 HEIC

接受 `image/jpeg`、`image/png`、`image/webp`；裝了 `pillow-heif` 且
`CLOUD_ALLOW_HEIC=true` 時另外接受 `image/heic`、`image/heif`。

**HEIC 例外**：存進 `media/cloud/` 的是**全解析度 JPEG 轉檔**（q95），不是原始 `.heic`。
原因是 `image_path` 會透過 `/media` 給瀏覽器讀，而主流瀏覽器不渲染 HEIC，
前端的欄位校正 UI 會顯示破圖。其他格式一律原樣寫入。

手機與部分 HTTP client 會把 HEIC 標成 `application/octet-stream`；
`CLOUD_SNIFF_CONTENT_TYPE=true`（預設）時不會立刻 400，而是交給 PIL 試解、失敗才 400。

---

## HTTP 狀態碼

| 情況 | 狀態 |
|---|---|
| MIME 不允許 / 無法解碼 | 400 |
| 上傳超過 `CLOUD_MAX_UPLOAD_BYTES` | 413 |
| 模型拒絕處理 / 輸出被截斷 / schema 不符 / provider 不接受該圖 | 422 |
| `OPENAI_API_KEY` 未設 / 連不上 provider / 被 rate limit | 503（帶 `Retry-After`） |
| provider 認證失敗 / 5xx | 502 |
| provider 逾時 | 504 |

驗證有未解決的 issue 時**仍回 200**（存 best-effort），欄位名放在
`X-Card-Validation-Issues` header——JSON body 與本機管線保持位元組相同。
要改成硬失敗設 `CLOUD_STRICT_VALIDATION=true`。

---

## 成本（實測，2048px、effort `low`）

2026-09-04 對 `test/` 全部 12 張實測（明細見 [`../benchmarks.md`](../benchmarks.md) §C）：

| 項目 | 實測平均 |
|---|---:|
| input tokens | 2,886 |
| output tokens（含 reasoning 240） | 500 |
| 延遲 | 6.0s（3.0–9.0s） |
| **每張** | **$0.00117（NT$0.038）** |
| validator issue / repair | **0 / 0**（12 張全數一次通過） |

| 情境 | 每張 | 100 張/天 | 200 張/天 |
|---|---:|---:|---:|
| 掃描圖（本批，多數 <2048px 未縮） | $0.00117 | US$3.5/月 | US$7.0/月 |
| 手機實拍推估（縮到 2048×1536） | ~$0.0016 | ~US$4.7/月 | ~US$9.4/月 |

加上 VPS（NT$130–250/月），200 張/天的**總持有成本約 NT$355–550/月**。

> **成本主導項是 reasoning effort，不是圖片大小。**
> 實測 reasoning tokens 佔 output 的 0–72%（0–516 tokens，變異極大）。
> `low`→`medium` 會顯著推高；`none` 可再省，但可能影響雜亂名片的 phone/mobile/fax 分類
> ——建議自己跑一輪 A/B 再決定（12 張 ×3 種 effort 總計 <US$0.10）。

對比 `../hybrid-scaling-plan.md` 的自建方案（NT$57,000 前期 + NT$1,000–1,500/月）：
要 **~757 張/天** 才追平 GPU 機的純營運成本、**~1,716 張/天** 才追平含 3 年攤提。

---

## 已知限制

- **沒有 fallback**。本機管線的 OCR 與分類是分開的兩段，所以 LLM 掛了還能退回 regex；
  這裡一次呼叫同時做兩件事，provider 掛了就連文字都沒有。**provider 可用性 = 管線可用性。**
- **驗證是自我一致性，不是跨引擎交叉檢查**。`raw_text` 由同一個模型產生，
  所以幻覺是相關的。它仍抓得到格式漂移、轉錄漏抄、跨欄位重複、整段捏造，
  但強度不如本機管線的 MinerU + Ollama 雙引擎。
- **不做繁簡轉換**。本機管線用 OpenCC `s2twp`（README 已記載那會過度轉換日文漢字）；
  這裡照卡片印刷原樣輸出，簡體名片會存簡體。這是刻意的保真度取捨。
- **regex 候選是台灣導向的**。外國電話格式常抓不到候選；管線用
  「值有沒有出現在轉錄裡」來區分「regex 認不得」與「模型漏抄」，避免系統性誤報。
- **SQLite 兩個 writer**。兩條管線同時對同一個 DB 高頻寫入可能撞鎖。
  100–200 張/天 機率極低；要完全隔離就在 `.env.cloud` 設不同的 `DATABASE_URL`，
  要共用則跑一次 `sqlite3 card_ocr.db "PRAGMA journal_mode=WAL;"`（持久設定，不需改程式）。
