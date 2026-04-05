# Business Card OCR API

接收名片圖片，自動識別並回傳結構化聯絡資訊的後台 API。

## 功能

- 上傳名片圖片（JPG / PNG / WebP）
- 自動 OCR 識別文字（支援中文、英文名片）
- 解析欄位：公司名、姓名、英文名、職稱、Email、市話、手機、地址、網站
- 儲存原圖與解析結果至本機
- 提供修正 API，供前端使用者手動修改欄位

---

## 環境需求

- macOS Apple Silicon（M1 / M2 / M3）
- Python 3.10（ARM64 版本，透過 ARM Homebrew 安裝）
- ARM Homebrew Python 路徑：`/opt/homebrew/bin/python3.10`

---

## 安裝步驟

### 方式 A：Rosetta 2（x86_64，較簡單）

適用 Apple Silicon Mac，透過 Rosetta 2 跑 x86_64 版本：

```bash
/usr/local/bin/python3 -m venv .venv   # 確認使用 x86_64 Python
source .venv/bin/activate
pip install paddlepaddle==2.6.2 paddleocr==2.6.1.3 --no-deps
pip install -r requirements-x86.txt
```

### 方式 B：ARM 原生安裝（較快，推薦）

```bash
/opt/homebrew/bin/python3.10 -m venv .venv_arm
source .venv_arm/bin/activate
pip install paddlepaddle==0.0.0 -f https://www.paddlepaddle.org.cn/whl/mac/cpu/develop.html
pip install paddleocr==2.6.1.3 --no-deps
pip install -r requirements-arm.txt
```

### 方式 C：Windows（x86_64，最新版模型）

需求：Python 3.10–3.12 **64-bit**（從 [python.org](https://python.org) 下載，勿選 32-bit）

確認 Python 為 64-bit：
```cmd
python -c "import struct; print(struct.calcsize('P')*8)"
```
應印出 `64`。

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install paddlepaddle==3.0.0
pip install paddleocr
pip install -r requirements-windows.txt
```

> Windows 使用 PaddleOCR 3.x（PP-OCRv5 模型），準確率優於 macOS 的 2.x 版本。

### 啟動 Server（開發模式）

```bash
uvicorn app.main:app --reload
```

首次上傳圖片時，PaddleOCR 會自動下載模型，下載後快取至本機，之後不需重複下載。

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
  "address": "831高雄市大寮區力行路197號",
  "website": null,
  "raw_text": "TOYOTA\n高都汽車股份有限公司\n...",
  "ocr_confidence": 0.9423,
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

## 專案結構

```
card_ocr/
├── app/
│   ├── main.py          # FastAPI 入口，CORS、靜態檔案、路由
│   ├── database.py      # SQLite 設定（SQLAlchemy）
│   ├── models/
│   │   └── card.py      # 資料庫 ORM model
│   ├── schemas/
│   │   └── card.py      # Pydantic 請求/回應 schema
│   ├── routers/
│   │   └── cards.py     # API 路由（upload / get / update）
│   └── services/
│       ├── ocr.py       # PaddleOCR 封裝（中英自動偵測）
│       └── parser.py    # Regex + 規則解析欄位
├── media/               # 上傳的名片圖片（git 忽略）
├── card_ocr.db          # SQLite 資料庫（git 忽略）
├── .venv/               # x86_64 虛擬環境（git 忽略）
├── .venv_arm/           # ARM 虛擬環境（git 忽略）
├── requirements-arm.txt     # ARM 安裝依賴（方式 B）
├── requirements-x86.txt     # x86 安裝依賴（方式 A）
├── requirements-windows.txt # Windows 安裝依賴（方式 C）
├── start.sh                 # 正式啟動腳本（macOS / Linux）
├── start.bat                # 正式啟動腳本（Windows）
├── stop.sh                  # 關閉 port 8000 的腳本（macOS / Linux）
├── .env.example             # 環境變數範本
└── .env                     # 環境設定（git 忽略）
```

---

## 常見錯誤與解法

### ❌ `Illegal instruction` — PaddlePaddle crash

**原因：** PyPI 的 `paddlepaddle` 只有 x86_64 wheel，在 Apple Silicon 上執行（即使透過 Docker `linux/amd64` 模擬）會因 AVX 指令集不相容而 crash。

**解法 1（簡單）：** 使用 Rosetta 2 + x86_64 Python 安裝一般版本：
```bash
/usr/local/bin/python3 -m venv .venv   # x86_64 Python
pip install paddlepaddle==2.6.2
```

**解法 2（ARM 原生）：** 使用官方 ARM develop 版本：
```bash
/opt/homebrew/bin/python3.10 -m venv .venv_arm
pip install paddlepaddle==0.0.0 -f https://www.paddlepaddle.org.cn/whl/mac/cpu/develop.html
```

---

### ❌ import paddle 卡住不動（ARM Python + x86 wheel）

**原因：** 用 ARM Python 安裝了 x86_64 的 paddlepaddle wheel，導致 import 卡死。

**確認方式：**
```bash
python3 -m platform   # 確認 Python 架構
```

**解法：** Python 與 paddlepaddle wheel 架構必須一致（都 x86 或都 ARM）。

---

### ❌ `show_log` argument error（新版 PaddleOCR）

**原因：** PaddleOCR 3.x 移除了 `show_log` 參數。

**解法：** 使用 PaddleOCR 2.6.x（`paddleocr==2.6.1.3`），或移除 `show_log=False`。

---

### ❌ `ModuleNotFoundError: No module named 'imgaug'`

**原因：** `paddleocr==2.6.1.3 --no-deps` 安裝後缺少 imgaug。

**解法：**
```bash
pip install imgaug
```

---

### ❌ numpy 版本衝突

**原因：** PaddlePaddle ARM dev 版需要 `numpy<2.0`，但部分套件（opencv-python-headless 4.13+）需要 `numpy>=2`。

**解法：** 安裝 `opencv-python<=4.6.0.66` 並鎖定 numpy：
```bash
pip install "numpy<2.0" "opencv-python<=4.6.0.66"
```

---

### ❌ `Failed building wheel for PyMuPDF` / `swig not found`

**原因：** `pip install paddleocr==2.6.1.3`（不加 `--no-deps`）會嘗試安裝 paddleocr 所有依賴，包含 `PyMuPDF`，而 PyMuPDF 需要系統工具 `swig` 才能編譯，一般環境沒有安裝。

**解法：** paddleocr 必須加 `--no-deps` 安裝，PDF 相關依賴不影響名片 OCR 功能：
```bash
pip install paddleocr==2.6.1.3 --no-deps
```

---

### ❌ Windows：`DLL load failed while importing` 或 import 失敗

**原因：** 缺少 Visual C++ Runtime。

**解法：** 安裝 [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)（vc_redist.x64.exe）後重新嘗試。

---

### ❌ Windows：安裝時提示 Python 不是 64-bit

**原因：** PaddlePaddle 3.x 只有 64-bit wheel，32-bit Python 無法安裝。

**確認方式：**
```cmd
python -c "import struct; print(struct.calcsize('P')*8)"
```
應印出 `64`。若印出 `32`，請重新從 [python.org](https://python.org) 下載 64-bit 版本。

---

## 已知限制

- OCR 對繁簡混用的名片可能有識別偏差（例如「銷售顧問」被識別為「销售厂周」），建議前端提供欄位修正功能
- PaddlePaddle ARM dev 版（`0.0.0`）為 nightly build，穩定性不如正式版
