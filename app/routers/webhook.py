# app/routers/webhook.py
from fastapi import APIRouter, Request, HTTPException
import os
from app.strategies.ichimoku_strategy import process_webhook_signal
from app.services.log_service import log_to_firestore

router = APIRouter()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


@router.post("/webhook/tradingview")
async def tradingview_webhook(request: Request):
    body = await request.json()

    # Validation du secret
    if WEBHOOK_SECRET and body.get("secret") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    log_to_firestore(f"[Webhook] Signal recu: {body}", level="WEBHOOK")

    result = process_webhook_signal(body)
    return result
