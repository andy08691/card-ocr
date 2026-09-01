"""
schemas/extraction.py — 本地 LLM 欄位擷取用的結構化 schema

- CardExtraction：LLM 要回傳的 10 個名片欄位。直接繼承 CardBase（schemas/card.py），
  保證欄位名/型別與 API 回應、DB ORM 三者永遠一致；全部 Optional[str]=None（方便建構與
  容錯 validate）。送給 Ollama 的 structured-output schema 會在 llm_parser 內把 10 個欄位
  全標成 required，強迫模型每欄都輸出（值或 null），避免小模型偷懶吐空物件。
- CardCandidates：candidate_extractor 用 regex 找出的候選值（只找、不分類），
  同時餵給 LLM（限定 email/phone/website 從候選中挑）與 validator（驗證 LLM 沒亂填）。
"""

from pydantic import BaseModel

from app.schemas.card import CardBase


class CardExtraction(CardBase):
    """LLM 擷取結果，10 個欄位與 CardBase / Card ORM 完全一致。"""
    pass


class CardCandidates(BaseModel):
    """regex 找出的候選值（只找不分類）。phones 為所有電話樣式的聯集。"""
    emails: list[str] = []
    phones: list[str] = []
    websites: list[str] = []
