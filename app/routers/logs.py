from fastapi import APIRouter, Query
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/logs")
def get_logs(limit: int = 50, level: str = Query(None), contains: str = Query(None)):
    db = get_firestore()

    # 🔍 Étendre le volume de recherche si on filtre par contenu
    search_limit = 1000 if contains else limit

    query = db.collection("execution_logs")

    if level:
        query = query.where("level", "==", level.upper())

    # ⏱️ ORDER BY après WHERE pour éviter problèmes d'index Firestore
    query = query.order_by("timestamp", direction="DESCENDING")

    docs = query.limit(search_limit).stream()
    results = []

    for doc in docs:
        data = doc.to_dict()

        # 🔎 Recherche multi-champs et multi-mots
        if contains:
            keywords = contains.lower().split()
            message = data.get("message", "").lower()
            timestamp = data.get("timestamp", "").lower()
            lvl = data.get("level", "").lower()

            if not all(
                any(kw in field for field in [message, timestamp, lvl])
                for kw in keywords
            ):
                continue

        results.append(data)

        # 🎯 Respecter la limite finale après filtre
        if len(results) >= limit:
            break

    return results
