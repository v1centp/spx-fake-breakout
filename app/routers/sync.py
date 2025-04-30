from fastapi import APIRouter
from app.services.bubble_sync_service import sync_trades_to_bubble

router = APIRouter()

@router.post("/sync-trades")
def sync_trades():
    sync_trades_to_bubble()
    return {"message": "✅ Trades synchronisés avec Bubble"}
