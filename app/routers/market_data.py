# app/routers/market_data.py

from fastapi import APIRouter
from pydantic import BaseModel
from app.services.firebase import get_firestore
from datetime import datetime, timezone

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
        "e": 1678220675805,
        "utc_time": datetime.fromtimestamp(1678220675805 / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }

    doc_id = str(candle["s"])
    db.collection("ohlc_1m").document(doc_id).set(candle)

    return {"message": "✅ SPX candle stored successfully"}
