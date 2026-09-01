# GPU 部署包（MinerU + Ollama + FastAPI）

把整個服務（OCR + 本地 LLM 欄位擷取）掛到雲端 GPU，驗證單張 5–10 秒。
一個容器同時跑 **Ollama(qwen3:4b)** 與 **FastAPI app**；GPU 上用 MinerU 的
`vlm-engine` 後端（避開 vLLM 依賴與 VRAM 競爭）。

## 檔案
| 檔 | 用途 |
|---|---|
| `Dockerfile` | GPU 映像（到處通用：Beam / RunPod / Cloud Run / Fly）|
| `entrypoint.sh` | 容器啟動：`ollama serve` → pull 模型 → `uvicorn`（綁 `$PORT`）|
| `beam_app.py` | Beam Pod 定義 |
| `lightning_setup.sh` / `lightning_run.sh` / `lightning.md` | **Lightning AI Studio 部署（推薦驗證用：免費、免卡、原樣跑、免 Docker）** |
| `../.dockerignore` | 建置時排除 venv/db/media 等大檔 |

> ⚠️ 第一次啟動會下載模型（qwen3:4b ~2.5GB + MinerU VLM ~2.5GB），約數分鐘；
> 掛了 Volume/持久磁碟後，之後冷啟就快。建議保持 1 個實例常駐（避開 15s 載入）。

---

## A. Beam（首選：免綁卡、送 $30、Pod 最貼合兩進程）

```bash
pip install beam-client
beam configure                       # 貼上 dashboard token（註冊即拿）
beam deploy deploy/beam_app.py:card_ocr
```
- GPU 在 `beam_app.py` 改 `gpu=`（`RTX4090` 建議 / `A10G` / `T4`）。
- `keep_warm_seconds=-1` = 永不休眠；要省錢改成秒數（會有冷啟動）。
- 模型走 `Volume` 快取，第一次 deploy 後就常駐。
- ⚠️ Beam SDK 介面隨版本變動，若 `Image`/`Pod` 參數對不上，`beam --help` / docs.beam.cloud 對照微調；核心邏輯在 `Dockerfile`+`entrypoint.sh`。

## B. RunPod Pods（原樣 Docker、最便宜、驗證最快）
```bash
# 本機建置並推到 registry（Docker Hub / GHCR）
docker build -f deploy/Dockerfile -t <you>/card-ocr-gpu:latest .
docker push <you>/card-ocr-gpu:latest
```
到 RunPod → 新 Pod → 選 GPU（RTX 3090/4090/L4）→ 用你的映像 → 對外開 port 8000
→ 掛一個 Volume 到 `/root/.cache/huggingface` 與 `/root/.ollama`（模型快取）。

## C. Google Cloud Run（GPU / L4、託管 HTTPS、$300 試用）
```bash
gcloud artifacts repositories create card-ocr --repository-format=docker --location=us-central1
docker build -f deploy/Dockerfile -t us-central1-docker.pkg.dev/<proj>/card-ocr/app:latest .
docker push us-central1-docker.pkg.dev/<proj>/card-ocr/app:latest

gcloud run deploy card-ocr \
  --image us-central1-docker.pkg.dev/<proj>/card-ocr/app:latest \
  --region us-central1 --gpu 1 --gpu-type nvidia-l4 \
  --cpu 4 --memory 16Gi --no-cpu-throttling \
  --min-instances 1 --max-instances 1 --port 8000 --allow-unauthenticated \
  --timeout 120
```
- Cloud Run 會注入 `$PORT`，entrypoint 已綁 `$PORT`。
- `--min-instances 1` 避開冷啟動；L4 需在該 region 有 GPU 配額。
- 模型建議改用 GCS FUSE 或接受首啟下載（Cloud Run 無持久磁碟）。

---

## 環境變數（各平台皆可覆寫）
| 變數 | 預設 | 說明 |
|---|---|---|
| `CARD_EXTRACTOR` | `llm` | `llm`＝走本地 LLM；`regex`＝一鍵回退純 regex |
| `MINERU_BACKEND` | `vlm-engine` | GPU 建議值；想用 vLLM 見下 |
| `OLLAMA_MODEL` | `qwen3:4b` | 換模型即改這裡 |
| `OLLAMA_KEEP_ALIVE` | `-1` | 模型常駐；省 VRAM 可設 `30m`/`0` |
| `MINERU_PDF_RENDER_THREADS` | `1` | 名片單頁，1 即可 |
| `PORT` | `8000` | 服務埠（Cloud Run 會覆寫）|

## 想榨最後一點速度：改用 vLLM（選配）
1. Dockerfile 改 `pip install "mineru[all]"`（含 vLLM）。
2. 設 `MINERU_BACKEND=vlm-engine`（CUDA 上自動選 vLLM）。
3. 為避免 vLLM 預佔 90% VRAM 餓死 Ollama，加 vLLM 參數
   `--gpu-memory-utilization 0.45 --max-model-len 8192 --max-num-seqs 1`
   （或設 `MINERU_VIRTUAL_VRAM_SIZE=8`）。
對 1.2B 模型，transformers 與 vLLM 的單張延遲差距不大，**建議先用預設 transformers**。

## 驗證
```bash
curl https://<your-endpoint>/health
curl -X POST https://<your-endpoint>/api/cards/upload -F "file=@card.jpg"
```
看 log 的 `MinerU OCR done: ... time=Xs`（MinerU 那段），總時間減它即 LLM 那段。
目標：整張落在 5–10 秒。
