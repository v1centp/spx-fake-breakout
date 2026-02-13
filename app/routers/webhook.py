# app/routers/webhook.py
from fastapi import APIRouter, Request, HTTPException
import os
from app.strategies.ichimoku_strategy import process_webhook_signal as ichimoku_process
from app.strategies.supply_demand_strategy import process_webhook_signal as supply_demand_process
from app.services.log_service import log_to_firestore

router = APIRouter()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

STRATEGY_DISPATCH = {
    "supply_demand": supply_demand_process,
}


@router.post("/webhook/tradingview")
async def tradingview_webhook(request: Request):
    body = await request.json()

    # Validation du secret
    if WEBHOOK_SECRET and body.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    log_to_firestore(f"[Webhook] Signal recu: {body}", level="WEBHOOK")

    # Dispatch par strategie (ichimoku par defaut pour retrocompatibilite)
    strategy = body.get("strategy", "ichimoku")
    handler = STRATEGY_DISPATCH.get(strategy, ichimoku_process)

    result = handler(body)
    return result
