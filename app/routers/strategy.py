# app/routers/strategy.py

from fastapi import APIRouter, BackgroundTasks
from app.services.strategy_service import start_spx_strategy

router = APIRouter()

@router.post("/start-strategy")
async def start_strategy(background_tasks: BackgroundTasks):
    background_tasks.add_task(start_spx_strategy)
    return {
        "message": "🚀 Stratégie SPX démarrée en arrière-plan"
    }
