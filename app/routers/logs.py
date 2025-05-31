from fastapi import APIRouter, Query
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/logs")
def get_logs(limit: int = 50, level: str = Query(None), contains: str = Query(None)):
    db = get_firestore()
    query = db.collection("execution_logs").order_by("timestamp", direction="DESCENDING")

    if level:
        query = query.where("level", "==", level.upper())

    docs = query.limit(limit).stream()
    results = []

    for doc in docs:
        data = doc.to_dict()
        if contains and contains.lower() not in data["message"].lower():
            continue
        results.append(data)

    return results
