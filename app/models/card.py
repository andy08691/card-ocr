from sqlalchemy import Column, Integer, String, Float, DateTime, func
from app.database import Base


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    image_path = Column(String, nullable=False)
    company_name = Column(String, nullable=True)
    person_name = Column(String, nullable=True)
    english_name = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    mobile = Column(String, nullable=True)
    fax = Column(String, nullable=True)
    address = Column(String, nullable=True)
    website = Column(String, nullable=True)
    raw_text = Column(String, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
