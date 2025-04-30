# app/routers/sync.py

from fastapi import APIRouter
from app.services.bubble_sync_service import sync_positions_to_bubble

router = APIRouter()

@router.post("/sync-positions")
def sync_positions():
    sync_positions_to_bubble()
    return {"message": "✅ Positions synced to Bubble DB"}
