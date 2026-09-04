"""
cloud/schemas/vision.py — 雲端 VLM 的結構化輸出 schema

CardVisionResult：模型一次回傳「逐字轉錄」+「10 個名片欄位」。
fields 直接用 app/schemas/extraction.py 的 CardExtraction，不另外定義一份——
那 10 個欄位的唯一來源是 CardBase，兩條管線與 ORM 永遠不會 drift。

與 app/services/llm_parser.py 的差異（值得記一筆，免得下個讀者困惑）：
  llm_parser 需要自己寫 _extraction_schema() 把 10 個欄位硬標成 required，
  因為 Ollama 直接吃 pydantic 產的 schema，而帶預設值的欄位不會列入 required，
  grammar 就允許模型吐空物件 {}。
  這裡用 OpenAI SDK 的 responses.parse(text_format=...)，SDK 的 to_strict_json_schema()
  已經幫我們把每一層都補上 required 與 additionalProperties: False，所以不必手刻。
  tests/test_cloud_vision.py 有測試把這個保證釘死。
"""

from pydantic import BaseModel

from app.schemas.extraction import CardExtraction


class CardVisionResult(BaseModel):
    """VLM 的回傳結構。

    raw_text 刻意排在 fields 前面：structured output 是照 schema 順序生成的，
    先逼模型把名片逐字抄一遍、再從自己抄的內容填欄位，等同 app/ 那條
    「OCR → 分類」的兩段式，只是壓在同一次呼叫裡；也讓下游的
    candidate_extractor / validator 有東西可以比對。

    不用 `class X(CardExtraction): raw_text: ...` 的扁平寫法，因為：
      1. pydantic 把子類別欄位接在最後 → 模型會先生成 10 個欄位、後生成轉錄，
         順序正好相反，接地（grounding）效果全失。
      2. router 的 Card(image_path=..., raw_text=..., **parsed) 會拿到重複的
         raw_text 關鍵字而 TypeError。巢狀版的 fields.model_dump() 天生剛好 10 個 key。
      3. validate_card(extraction: CardExtraction, ...) 收到的是真正的 CardExtraction，
         不是被加寬的子類別。
    """

    raw_text: str          # 必填、不可為 null：沒有轉錄，第二關就沒有東西可驗
    fields: CardExtraction


class CloudPipelineResult(BaseModel):
    """card_pipeline 的輸出，router 據此組 Card 與回應 header。"""

    fields: dict           # 恰好 10 個 key，可直接 Card(**fields)
    raw_text: str
    ocr_confidence: float
    issues: list           # 修不掉的 ValidationIssue（供 log / X-Card-Validation-Issues）
    usage: dict            # input/cached/output/reasoning tokens + repair 次數（成本記帳）
