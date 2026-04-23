"""
schemas/card.py — Pydantic 請求 / 回應 schema

三個 schema 的用途：
  CardBase     — 所有可編輯的解析欄位，CardUpdate 與 CardResponse 共用
  CardUpdate   — PUT /api/cards/{id} 的請求 body（欄位全部可選，只更新有傳的）
  CardResponse — 任何端點的回應格式（含系統欄位 id、image_path、created_at）

新增欄位時，三個 schema 以及 models/card.py 都需要同步更新。
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CardBase(BaseModel):
    """可由前端修正的解析欄位，全部為 Optional（null 代表未識別）。"""
    company_name: Optional[str] = None    # 公司名稱
    person_name: Optional[str] = None     # 姓名
    english_name: Optional[str] = None   # 英文姓名
    job_title: Optional[str] = None      # 職稱
    email: Optional[str] = None          # 電子郵件
    phone: Optional[str] = None          # 市話
    mobile: Optional[str] = None         # 手機
    fax: Optional[str] = None            # 傳真
    address: Optional[str] = None        # 地址
    website: Optional[str] = None        # 網址


class CardUpdate(CardBase):
    """PUT /api/cards/{id} 的 request body。
    繼承 CardBase，所有欄位均為 Optional，
    實際只更新有傳入的欄位（model_dump(exclude_unset=True)）。
    """
    pass


class CardResponse(CardBase):
    """所有 GET / POST 回應的格式，在 CardBase 之上加入唯讀系統欄位。"""
    id: int                                        # 資料庫主鍵
    image_path: str                                # 名片圖片路徑（/media/xxxx.jpg）
    raw_text: Optional[str] = None                 # OCR 識別的完整原始文字
    ocr_confidence: Optional[float] = None         # OCR 平均信心分數（0.0–1.0）
    created_at: Optional[datetime] = None          # 建立時間

    class Config:
        # 允許直接從 SQLAlchemy ORM 物件建立（from_orm 模式）
        from_attributes = True
