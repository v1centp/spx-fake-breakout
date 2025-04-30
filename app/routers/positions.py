# app/routers/positions.py

from fastapi import APIRouter
from app.services.oanda_service import get_open_positions

router = APIRouter()

@router.get("/open-positions")
async def open_positions():
    positions = get_open_positions()
    return {
        "message": "✅ Positions ouvertes récupérées",
        "positions": positions
    }
