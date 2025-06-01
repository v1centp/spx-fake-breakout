# app/routers/market_data.py

from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.services.firebase import get_firestore
from datetime import datetime, timezone
from typing import List
from app.services.oanda_service import get_latest_price, create_order
from app.services import strategy_logic
from fastapi import Request

router = APIRouter()

class TestCandleRequest(BaseModel):
    test: bool
    
class OrderRequest(BaseModel):
    instrument: str
    units: int

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
 
@router.get("/latest-price")
def latest_price(instrument: str = Query(...)):
   try:
      price = get_latest_price(instrument)
      return {"price": price}
   except Exception as e:
      return {"error": str(e)}
   
@router.get("/spx-price")
def get_spx_price():
    try:
        price = get_latest_price("SPX500_USD")
        return {"instrument": "SPX500_USD", "price": price}
    except Exception as e:
        return {"error": str(e)}

@router.get("/instruments")
def get_instruments():
    from app.services.oanda_service import list_instruments
    try:
        instruments = list_instruments()
        # Optionnel : filtrer pour afficher seulement les CFDs (indices, or, etc.)
        return {"instruments": instruments}
    except Exception as e:
        return {"error": str(e)}
     
@router.post("/create-order")
def api_create_order(req: OrderRequest):
    try:
        result = create_order(req.instrument, req.units)
        return {"message": "✅ Order sent", "details": result}
    except Exception as e:
        return {"error": str(e)}

@router.post("/test-order")
async def test_fake_breakout(req: Request):
    data = await req.json()
    direction = data.get("direction")  # "LONG" ou "SHORT"

    # Simule une bougie avec un faux breakout pour forcer un signal
    bar = {
        "day": datetime.now().strftime("%Y-%m-%d"),
        "utc_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "o": 5000,
        "h": 5100 if direction == "SHORT" else 5050,
        "l": 4900 if direction == "LONG" else 4950,
        "c": 5020,  # close dans le range
        "s": "I:SPX",
        "e": "minute",
        "in_opening_range": False
    }

    strategy_logic.process_new_minute_bar(bar)
    return {"message": f"🚀 Test ordre {direction} déclenché"}