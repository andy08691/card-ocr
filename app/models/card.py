"""
models/card.py — SQLAlchemy ORM model

對應資料庫的 `cards` 資料表。
每次新增欄位後，若資料庫已存在，需手動執行：
    sqlite3 card_ocr.db "ALTER TABLE cards ADD COLUMN 欄位名 TEXT;"
（SQLAlchemy 的 create_all 只建表，不自動 ALTER 已存在的表）
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, func
from app.database import Base


class Card(Base):
    __tablename__ = "cards"

    # ── 系統欄位 ──────────────────────────────────────────────────────────────
    id = Column(Integer, primary_key=True, index=True)
    image_path = Column(String, nullable=False)          # 圖片相對路徑，如 media/xxxx.jpg
    raw_text = Column(String, nullable=True)             # OCR 原始文字（換行分隔）
    ocr_confidence = Column(Float, nullable=True)        # OCR 平均信心分數（0.0–1.0）
    created_at = Column(DateTime, server_default=func.now())  # 建立時間（DB server 自動填入）

    # ── 解析欄位（均可為 null，OCR 無法識別時回傳 null）─────────────────────
    company_name = Column(String, nullable=True)   # 公司名稱
    person_name = Column(String, nullable=True)    # 姓名（中文或英文）
    english_name = Column(String, nullable=True)   # 英文姓名（中文名片上的英文名）
    job_title = Column(String, nullable=True)      # 職稱
    email = Column(String, nullable=True)          # 電子郵件
    phone = Column(String, nullable=True)          # 市話 / 公司電話
    mobile = Column(String, nullable=True)         # 手機號碼
    fax = Column(String, nullable=True)            # 傳真號碼
    address = Column(String, nullable=True)        # 地址
    website = Column(String, nullable=True)        # 網址
