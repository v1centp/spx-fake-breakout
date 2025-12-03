from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.services.firebase import get_firestore
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
