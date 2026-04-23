"""
database.py — SQLite 資料庫設定

使用 SQLAlchemy ORM，資料庫路徑由 .env 的 DATABASE_URL 控制。
預設路徑：card_ocr.db（與專案根目錄同層）。

若要換成其他資料庫（PostgreSQL、MySQL），只需修改 .env 的 DATABASE_URL，
並移除 connect_args（該參數只有 SQLite 需要）。
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

# 資料庫連線字串，預設 SQLite 放在專案根目錄
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./card_ocr.db")

# check_same_thread=False：允許 FastAPI 多執行緒共用同一個 SQLite 連線
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# autocommit=False：需要手動 db.commit()，確保資料完整性
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 所有 ORM model 繼承此 Base，Base.metadata.create_all() 會依此建立資料表
Base = declarative_base()


def get_db():
    """FastAPI dependency：每個 request 取得一個 DB session，結束後自動關閉。

    使用方式：
        @router.post("/...")
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
