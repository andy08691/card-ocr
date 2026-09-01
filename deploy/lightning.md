# 在 Lightning AI 部署（驗證 5–10 秒）

Lightning 的 **Studio** = 持久 Linux + GPU 開發機，**不需要 Docker**。把 repo 原樣跑起來、
把 port 8000 對外分享，就等於部署好一個可測試的服務。免費、免綁卡、Google/GitHub 登入。

## 步驟

### 1. 註冊 + 開 GPU Studio
1. 到 **https://lightning.ai** → Sign up（**Google / GitHub 登入**，不寄認證信）。
2. 進 dashboard → **New Studio**（或用預設 Studio）。
3. 右側切換運算資源 → 選 **GPU**：
   - **免費且不綁卡 = 只有 T4（16GB）**。先用 T4 驗證即可——它是保守下限，
     單張大概 ~6–12 秒；若 T4 就能到 ~10s，你要買的中階卡一定更快。
   - 想測更快的 **L4（24GB, $0.48/hr）/ A100**，需先在 Lightning 驗證付款方式（綁卡，
     額度內通常不扣款）。
   T4 16GB 放得下 MinerU + qwen3:4b（用 `vlm-engine` 後端，預設值即可）。

### 2. 取得程式碼
Studio 終端機裡：
```bash
git clone <你的 repo 網址> card_ocr && cd card_ocr
# 若 repo 是私有：用 gh auth / PAT，或直接把專案資料夾上傳到 Studio
```

### 3. 一次性安裝
```bash
bash deploy/lightning_setup.sh
```
會裝 Ollama + 建 venv + 裝 `mineru[core]` 與 app 依賴 + 下載 qwen3:4b 與 MinerU 模型。
（第一次下載模型 ~5GB，數分鐘。）

### 4. 啟動服務
```bash
bash deploy/lightning_run.sh
```
看到 `MinerU warmup done` 與 `Ollama warmup done` 就緒。

### 5. 對外開 port 8000（在 Studio 介面）
在 Studio 找 **Ports / “Share port” / 網路** 的設定，把 **8000** 對外開放，
Lightning 會給你一個公開網址。（此步是在網頁介面點，不是指令。）

### 6. 測試
```bash
curl https://<lightning給你的網址>/health
curl -X POST https://<lightning給你的網址>/api/cards/upload -F "file=@test/test_card1.png"
```
或直接開 `https://<網址>/docs` 用瀏覽器上傳。看單張是否落在 **5–10 秒**。

> 只想量速度、不想開 port？在終端機直接跑一支計時腳本呼叫 `run_ocr` + `extract_card`
> 即可（我可以幫你寫）。看 log 的 `MinerU OCR done ... time=Xs`，總時間減它＝LLM 那段。

---

## 換 GPU 比較
在 Studio 右側切換 GPU（T4 → L4 → A100），重跑步驟 4 再測，即可比較不同卡的單張延遲，
直接幫你決定要買哪張自建（T4=保守下限、L4/A100=上限）。

## 環境變數（可覆寫）
`CARD_EXTRACTOR=llm|regex`、`MINERU_BACKEND=vlm-engine`（GPU 建議）、
`OLLAMA_MODEL=qwen3:4b`、`OLLAMA_KEEP_ALIVE=-1`。

## 持久化備註
- Studio 的家目錄會持久保存；模型快取在 `~/.cache/huggingface`（MinerU）與 `~/.ollama`（LLM），
  重啟 Studio 通常免重抓。
- 若你發現重啟後模型又重抓，代表持久區在別的路徑（如 `/teamspace/studios/this_studio`）——
  把快取指過去即可：
  ```bash
  export HF_HOME=/teamspace/studios/this_studio/.hf
  export OLLAMA_MODELS=/teamspace/studios/this_studio/.ollama
  ```
  （在 setup / run 前 export，或寫進 Studio 的環境設定。）

## 限制（為何只適合驗證、不適合當正式服務）
- 免費「常駐」Studio **每 4 小時重啟**、spot 機器可能被搶佔 → 驗證/開發 OK，非 24/7 SLA。
- 要做自動擴縮的正式對外端點，之後再回 RunPod / Cloud Run 或自建（用 `deploy/Dockerfile`）。
