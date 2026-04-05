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

### 1. 建立 ARM64 虛擬環境

```bash
/opt/homebrew/bin/python3.10 -m venv .venv_arm
.venv_arm/bin/pip install --upgrade pip
```

### 2. 安裝 PaddlePaddle（ARM 版本）

> ⚠️ PyPI 上的 `paddlepaddle` 沒有 ARM wheel，需從官方特定網址安裝 develop 版

```bash
.venv_arm/bin/pip install paddlepaddle==0.0.0 -f https://www.paddlepaddle.org.cn/whl/mac/cpu/develop.html
```

### 3. 安裝 PaddleOCR 及其他依賴

```bash
.venv_arm/bin/pip install paddleocr==2.6.1.3 --no-deps
.venv_arm/bin/pip install \
    "numpy<2.0" \
    "opencv-python<=4.6.0.66" \
    "opencv-contrib-python<=4.6.0.66" \
    pyclipper shapely lmdb rapidfuzz imgaug \
    scikit-image tqdm requests pillow
.venv_arm/bin/pip install \
    fastapi "uvicorn[standard]" sqlalchemy \
    python-multipart python-dotenv
```

### 4. 啟動 Server

```bash
.venv_arm/bin/uvicorn app.main:app --reload
```

首次啟動並上傳圖片時，PaddleOCR 會自動下載模型（約 18MB），下載後快取至 `~/.paddleocr/`，之後不需重複下載。

---

## API 使用方式

啟動後開啟 Swagger UI：`http://localhost:8000/docs`

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
├── .venv_arm/           # ARM64 虛擬環境（git 忽略）
├── requirements.txt
└── .env
```

---

## 常見錯誤與解法

### ❌ `Illegal instruction` — PaddlePaddle crash

**原因：** PyPI 的 `paddlepaddle` 只有 x86_64 wheel，在 Apple Silicon 上執行（即使透過 Docker `linux/amd64` 模擬）會因 AVX 指令集不相容而 crash。

**解法：** 使用官方 ARM develop 版本安裝：
```bash
pip install paddlepaddle==0.0.0 -f https://www.paddlepaddle.org.cn/whl/mac/cpu/develop.html
```

---

### ❌ import paddle 卡住不動

**原因：** 使用了 x86_64 Python（`/usr/local/bin/python3`），即使安裝了 ARM paddle 也無法正常載入。

**確認方式：**
```bash
python3 -m platform
# 必須顯示 arm64，例如：macOS-14.x-arm64-arm-64bit
```

**解法：** 改用 ARM Homebrew Python：
```bash
/opt/homebrew/bin/python3.10 -m venv .venv_arm
```

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

## 已知限制

- OCR 對繁簡混用的名片可能有識別偏差（例如「銷售顧問」被識別為「销售厂周」），建議前端提供欄位修正功能
- PaddlePaddle ARM dev 版（`0.0.0`）為 nightly build，穩定性不如正式版
