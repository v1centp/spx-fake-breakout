# app/routers/market_data.py

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.firebase import get_firestore
from datetime import datetime, timezone
from typing import List

router = APIRouter()

class TestCandleRequest(BaseModel):
    test: bool

@router.post("/store-candle")
async def store_sample_candle(req: TestCandleRequest):
    db = get_firestore()

    candle = {
        "ev": "AM",
        "sym": "I:SPX",
        "op": 3985.67,
        "o": 3985.67,
        "c": 3985.67,
        "h": 3985.67,
        "l": 3985.67,
        "s": 1678220675805,
        "e": 1678220675806,
        "utc_time": datetime.fromtimestamp(1678220675805 / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }

    doc_id = str(candle["s"])
    db.collection("ohlc_1m").document(doc_id).set(candle)

    return {"message": "✅ SPX candle stored successfully"}
 
@router.get("/candles", response_model=List[dict])
async def get_candles(limit: int = 100000):
    db = get_firestore()
    docs = db.collection("ohlc_1m").order_by("s", direction="DESCENDING").limit(limit).stream()
    return [doc.to_dict() for doc in docs]

@router.get("/opening_range/{day}")
def get_opening_range(day: str):
    db = get_firestore()
    doc = db.collection("opening_range").document(day).get()
    if doc.exists:
        return doc.to_dict()
    return {"message": "Not found"}, 404