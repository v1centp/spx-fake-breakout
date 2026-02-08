from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.services.firebase import get_firestore
from app.services import oanda_service
from datetime import datetime, timezone
from typing import List

router = APIRouter()

# ✅ Endpoint pour consulter les dernières bougies stockées
@router.get("/candles", response_model=List[dict])
async def get_candles(day: str):
    db = get_firestore()
    query = db.collection("ohlc_1m").where("day", "==", day).order_by("s")
    docs = query.stream()
    return [doc.to_dict() for doc in docs]


# ✅ Endpoint pour récupérer le range d'ouverture d'un jour donné
# ⚠️ Les documents Firestore sont nommés "YYYY-MM-DD_I:<SYMBOL>" (ex: 2025-12-03_I:SPX)
# On autorise un paramètre instrument (par défaut SPX) et on fallback sur une requête where("day" == ...)
@router.get("/opening_range/{day}")
def get_opening_range(day: str, instrument: str = Query("SPX", description="Instrument, ex: SPX/NDX/DJI/RUT")):
    db = get_firestore()

    # 1) Essayer le doc avec suffixe instrument
    doc_id = f"{day}_I:{instrument}"
    doc = db.collection("opening_range").document(doc_id).get()
    if doc.exists:
        return doc.to_dict()

    # 2) Fallback : chercher par champ "day" si le doc est nommé autrement
    query = db.collection("opening_range").where("day", "==", day).limit(1)
    docs = list(query.stream())
    if docs:
        return docs[0].to_dict()

    return {"message": "Not found"}, 404


@router.get("/candles/oanda")
async def get_oanda_candles(
    instrument: str = Query(..., description="OANDA instrument, e.g. EUR_USD"),
    day: str = Query(..., description="Date YYYY-MM-DD"),
    granularity: str = Query("M5", description="Candle granularity, e.g. M1, M5, M15, H1"),
):
    from_time = f"{day}T00:00:00Z"
    to_time = f"{day}T23:59:59Z"
    candles = oanda_service.get_candles(instrument, from_time, to_time, granularity)
    return candles
