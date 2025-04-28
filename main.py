# fichier: main.py
from fastapi import FastAPI, BackgroundTasks
from datetime import datetime
import asyncio
import pytz

app = FastAPI()

ny_tz = pytz.timezone('America/New_York')  # Timezone de New York

# Fonction principale de ta stratégie
async def run_trading_strategy():
    print(">>> Démarrage de la stratégie SPX...")

    # 1. Attendre jusqu'à 09:30 New York
    while True:
        now_ny = datetime.now(ny_tz)
        if now_ny.hour == 9 and now_ny.minute >= 30:
            break
        await asyncio.sleep(5)

    print(">>> 09:30 NY atteint : début de la collecte du range")

    # 2. Simuler la collecte du range 09:30 - 09:45
    await asyncio.sleep(15 * 60)  # 15 minutes (en réalité tu collectes ici)

    print(">>> 09:45 NY atteint : fin de la collecte du range, début du trading")

    # 3. Surveiller les signaux jusqu'à 11:30 NY
    while True:
        now_ny = datetime.now(ny_tz)
        if now_ny.hour == 11 and now_ny.minute >= 30:
            break
        await asyncio.sleep(60)  # Analyse toutes les minutes

    print(">>> 11:30 NY atteint : arrêt de la session de trading")

@app.post("/start-strategy")
async def start_strategy(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_trading_strategy)
    return {"message": "Stratégie SPX démarrée en arrière-plan"}
