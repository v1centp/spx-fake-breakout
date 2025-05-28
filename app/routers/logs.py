from fastapi import APIRouter
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/logs")
def get_logs(limit: int = 50):
    db = get_firestore()
    docs = db.collection("execution_logs").order_by("timestamp", direction="DESCENDING").limit(limit).stream()
    return [doc.to_dict() for doc in docs]
