import logging

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from app.database import Base, engine, get_db
from app.routers.cards import router as cards_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Business Card OCR API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

media_dir = os.getenv("MEDIA_DIR", "media")
os.makedirs(media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

app.include_router(cards_router)


@app.get("/")
def root():
    return {"message": "Business Card OCR API is running"}


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
