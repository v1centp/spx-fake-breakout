from fastapi import FastAPI
import requests
from dotenv import load_dotenv
import os

# Charger les variables d'environnement
load_dotenv()

app = FastAPI()

# Récupérer les variables
OANDA_API_URL = os.getenv("OANDA_API_URL")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

headers = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json"
}

@app.post("/check-balance")
async def check_balance():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/summary"
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        return {"message": "❌ Impossible de récupérer la balance.", "status_code": response.status_code}
    
    data = response.json()
    balance = data["account"]["balance"]

    return {
        "message": "✅ Solde récupéré avec succès",
        "balance": balance
    }
