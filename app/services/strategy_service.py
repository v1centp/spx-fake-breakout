# app/services/strategy_service.py

import asyncio

async def start_spx_strategy():
    print(">>> [STRATEGY] Démarrage de la stratégie SPX...")
    await asyncio.sleep(2)
    print(">>> [STRATEGY] En attente de la session 09:30 NY...")
    await asyncio.sleep(3)
    print(">>> [STRATEGY] Logique de stratégie à implémenter ici.")
    print(">>> [STRATEGY] Stratégie terminée.")
