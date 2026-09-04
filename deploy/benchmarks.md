# OCR 效能實測記錄（Benchmarks）

> 記錄不同硬體上「單張名片端到端」的耗時，作為選硬體與擴充規劃的依據。
> 管線：`圖片 → MinerU(OCR) → extract_card(candidate → 本地 LLM 分類 → validator)`。
> LLM = 本機 Ollama `qwen3:4b`。
>
> 最後更新：2026-09-02

---

## 摘要（暖機後、單張、序列處理）

| 硬體 | OCR 平均 | LLM 平均 | **總計平均** | 範圍 | 後端 |
|---|---|---|---|---|---|
| **Apple M2 Pro / 16GB**（我的電腦） | ~10.6s | ~5.9s | **~16.6s** | 14.2–20.2s | MinerU MLX + Ollama |
| **Lightning AI L4 / 24GB**（雲端） | ~3.4s | ~1.8s | **~5.2s** | 4.7–6.1s | MinerU vLLM 0.10.2 + Ollama |

**→ L4 比 Mac 快約 3.2×**（OCR 3.2×、LLM ~3×）。OCR 一直是大頭；上 GPU 的 vLLM batching 是最大槓桿。

---

## A. Apple M2 Pro / 16GB（本機開發環境）

- 系統：Apple M2 Pro / 16GB / macOS 14.7.2
- 後端：MinerU MLX（`vlm-engine` 自動選 mlx）+ Ollama `qwen3:4b`
- 暖機（僅啟動一次）：MinerU **12.77s**、LLM **6.05s**
- 資料來源：本機 benchmark 腳本（6 張名片，含中/英/日）

| 名片 | 語言 | boxes | OCR (s) | LLM (s) | 總計 (s) |
|---|---|---|---|---|---|
| test_card | zh | 7 | 11.04 | 3.72 | 14.77 |
| test_card1 | zh | 12 | 11.31 | 5.71 | 17.02 |
| test_card2 | zh | 11 | 11.04 | 5.80 | 16.83 |
| test_card3 | zh | 9 | 9.61 | 6.77 | 16.38 |
| test_card4 | en | 8 | 8.96 | 5.28 | 14.24 |
| 275709_0 | zh | 13 | 11.83 | 8.36 | 20.19 |
| **平均** | | | **10.63** | **5.94** | **16.57** |

**觀察：**
- OCR ~10.6s，佔總時間 **~64%**——瓶頸在 OCR，不在 LLM。
- 最慢的是日文名片（275709_0，20.2s），LLM 分類也最久（8.4s），符合「非標準名片較吃分類」。

---

## B. Lightning AI L4 / 24GB（雲端 GPU）

- 平台：Lightning AI Studio，GPU = NVIDIA **L4 24GB**
- 後端：MinerU **vLLM 0.10.2**（`vlm-engine`，continuous decode）+ Ollama `qwen3:4b`
- 環境：`MINERU_BACKEND=vlm-engine`、`MINERU_VIRTUAL_VRAM_SIZE=8`（vLLM 與 Ollama 共存）
- 資料來源：Lightning 執行 log（`test_card1.png`，重複上傳）

| 量測 | 值 |
|---|---|
| MinerU OCR（log `time=`，精確） | 4.09s / 2.92s / 3.06s → 平均 **~3.4s** |
| 端到端總計（暖機後） | 6.06s / 4.70s / 4.85s → 平均 **~5.2s** |
| LLM（總計 − OCR，推估） | **~1.5–2s** |

**觀察：**
- 暖機後穩定落在 **~5 秒**，達成「5–10 秒」目標。
- OCR 從 Mac 的 ~10.6s → **~3.4s**；LLM 從 ~5.9s → **~2s**。
- 準確度：test_card1 有 6/7 欄位正確；唯一漏抓為全形括號市話 `電話（04）…`（`qwen3:4b` 分類缺口，見 README「已知限制」）。

> 註：L4 端到端總計取自 log 的「上傳→200 OK」時間戳（秒級解析度，故列為 ~），
> MinerU OCR 的 `time=` 為程式內精確計時。

---

## 待補（Roadmap 相關）

- [ ] **L4 併發吞吐**：改 vLLM server 模式（`vlm-http-client`）後，開多併發實測單卡「張/分」
      → 驗證 `hybrid-scaling-plan.md` §1 的「單卡 batching 2–4×」。
- [ ] **RTX 3090 / 4070 Ti Super**：買卡後補自建機的單張與併發數據。
- [ ] **雲端 burst worker**（RunPod/Modal）冷啟動時間量測。

---

相關文件：[`hybrid-scaling-plan.md`](hybrid-scaling-plan.md)（擴充架構與成本）、[`../README.md`](../README.md)（架構總覽）
