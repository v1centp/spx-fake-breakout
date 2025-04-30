# app/routers/orders.py

from fastapi import APIRouter
from app.services.oanda_service import create_order, close_order

router = APIRouter()

@router.post("/open-order")
async def open_order(instrument: str, units: int):
    order = create_order(instrument, units)
    return {
        "message": "✅ Ordre ouvert avec succès",
        "order": order
    }

@router.post("/close-order")
async def close_position(instrument: str):
    result = close_order(instrument)
    return {
        "message": "✅ Position fermée avec succès",
        "result": result
    }
