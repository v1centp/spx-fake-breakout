# app/routers/balance.py

from fastapi import APIRouter
from app.services.oanda_service import get_account_balance

router = APIRouter()

@router.get("/check-balance")
async def check_balance():
    balance = get_account_balance()
    return {
        "message": "✅ Solde récupéré avec succès",
        "balance": balance
    }
