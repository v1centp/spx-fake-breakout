# app/routers/positions.py

from fastapi import APIRouter
from app.services import oanda_service

router = APIRouter()

@router.get("/positions")
def get_positions():
    try:
        positions = oanda_service.get_open_positions()
        return {"positions": positions}
    except Exception as e:
        return {"error": str(e)}
