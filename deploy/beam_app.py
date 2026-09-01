"""
deploy/beam_app.py — Beam Pod 定義（把 MinerU + Ollama + FastAPI 掛上 GPU）

部署：
    pip install beam-client
    beam configure           # 貼上 dashboard 的 token（註冊即拿，通常免綁卡、送 $30）
    beam deploy deploy/beam_app.py:card_ocr

⚠️ 重要：Beam 的 SDK 介面會隨版本演進，此檔依「Pod + Image + Volume」的文件化用法撰寫，
   但我無法在此環境實際 deploy 驗證。第一次部署請對照 `beam --help` 與 docs.beam.cloud，
   若 Image/Pod 參數名有出入再微調。真正到處通用、確定可靠的核心是
   deploy/Dockerfile + deploy/entrypoint.sh（RunPod / Cloud Run 直接用那份即可）。

設計對齊 Dockerfile：GPU 上走 MinerU 的 vlm-engine 後端（無 vllm 時自動退回 transformers，避開 vLLM 依賴與 VRAM 競爭）。
模型放在 Volume 快取：第一次啟動下載（qwen3:4b ~2.5GB + MinerU VLM ~2.5GB），之後秒起。
"""

from beam import Image, Pod, Volume

# ── 模型快取（掛 Volume，避免每次冷啟重抓）────────────────────────────────────
hf_cache = Volume(name="mineru-hf-cache", mount_path="/root/.cache/huggingface")
ollama_cache = Volume(name="ollama-models", mount_path="/root/.ollama")

# ── 映像：等同 deploy/Dockerfile 的內容（Beam 用自己的 Image API 建置）──────────
image = (
    Image(
        base_image="nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04",
        python_version="python3.10",
    )
    .add_commands(
        [
            "apt-get update && apt-get install -y --no-install-recommends "
            "curl ca-certificates libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*",
            "curl -fsSL https://ollama.com/install.sh | sh",
            # vLLM 釘 0.10.2（0.28+ 會讓 MinerU 輸出亂碼）；用 uv 避開 pip backtracking。
            "pip install -U pip uv && uv pip install --system 'mineru[core]' 'vllm==0.10.2'",
        ]
    )
    # 應用層依賴：直接列出（等同 requirements.txt 的非 MinerU 部分）
    .add_python_packages(
        [
            "fastapi",
            "uvicorn[standard]",
            "python-multipart",
            "sqlalchemy",
            "python-dotenv",
            "opencc-python-reimplemented",
            "pillow",
            "ollama",
        ]
    )
)

# ── Pod：常駐 GPU 容器，跑 entrypoint（兩進程）──────────────────────────────────
# gpu 可換："RTX4090"(24G,建議) / "A10G"(24G) / "T4"(16G,較慢)。
# keep_warm_seconds=-1 → 永不休眠（避開 15s 模型載入冷啟動）；要省錢可改成秒數。
# Beam 部署時會同步當前工作目錄（含 app/、deploy/），故容器內可直接跑 app.main。
card_ocr = Pod(
    name="card-ocr",
    image=image,
    gpu="RTX4090",
    ports=[8000],
    volumes=[hf_cache, ollama_cache],
    keep_warm_seconds=-1,
    env={
        "CARD_EXTRACTOR": "llm",
        "MINERU_BACKEND": "vlm-engine",
        "MINERU_VIRTUAL_VRAM_SIZE": "8",   # 限 vLLM ~8GB，留 VRAM 給 Ollama
        "MINERU_PDF_RENDER_THREADS": "1",
        "OLLAMA_MODEL": "qwen3:4b",
        "OLLAMA_KEEP_ALIVE": "-1",
        "PORT": "8000",
    },
    entrypoint=["bash", "deploy/entrypoint.sh"],
)
