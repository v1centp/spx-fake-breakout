# app/routers/balance.py

from fastapi import APIRouter
from app.services.oanda_service import get_account_balance
from app.services.log_service import log_to_slack

router = APIRouter()

@router.get("/check-balance")
async def check_balance():
    balance = get_account_balance()
    log_to_slack("✅ test slack from client", level="TRADING")
    return {
        "message": "✅ Solde récupéré avec succès",
        "balance": balance
    }
