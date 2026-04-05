from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CardBase(BaseModel):
    company_name: Optional[str] = None
    person_name: Optional[str] = None
    english_name: Optional[str] = None
    job_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None


class CardUpdate(CardBase):
    pass


class CardResponse(CardBase):
    id: int
    image_path: str
    raw_text: Optional[str] = None
    ocr_confidence: Optional[float] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
