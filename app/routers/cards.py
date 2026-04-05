import os
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.card import Card
from app.schemas.card import CardResponse, CardUpdate
from app.services.ocr import run_ocr
from app.services.parser import parse_card

router = APIRouter(prefix="/api/cards", tags=["cards"])

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
MEDIA_DIR = os.getenv("MEDIA_DIR", "media")


@router.post("/upload", response_model=CardResponse)
async def upload_card(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: jpg, png, webp",
        )

    # Save image to media/
    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
    filename = f"{uuid.uuid4()}.{ext}"
    image_path = os.path.join(MEDIA_DIR, filename)
    os.makedirs(MEDIA_DIR, exist_ok=True)

    contents = await file.read()
    with open(image_path, "wb") as f:
        f.write(contents)

    # Run OCR in thread pool (blocking operation)
    ocr_result = await run_in_threadpool(run_ocr, image_path)

    # Parse structured fields
    parsed = parse_card(
        raw_text=ocr_result["raw_text"],
        boxes=ocr_result["boxes"],
        lang=ocr_result["lang"],
    )

    # Save to DB
    card = Card(
        image_path=image_path,
        raw_text=ocr_result["raw_text"],
        ocr_confidence=ocr_result["ocr_confidence"],
        **parsed,
    )
    db.add(card)
    db.commit()
    db.refresh(card)

    return card


@router.get("/{card_id}", response_model=CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


@router.put("/{card_id}", response_model=CardResponse)
def update_card(card_id: int, updates: CardUpdate, db: Session = Depends(get_db)):
    card = db.query(Card).filter(Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(card, field, value)

    db.commit()
    db.refresh(card)
    return card
