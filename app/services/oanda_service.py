# app/services/oanda_service.py

import os
import requests
from dotenv import load_dotenv

from app.services.log_service import log_to_firestore

load_dotenv()

OANDA_API_URL = os.getenv("OANDA_API_URL")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

headers = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json"
}


def get_account_balance():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/summary"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return float(response.json()["account"]["balance"])


def get_open_trades():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/openTrades"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get("trades", [])


def get_open_positions():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/openPositions"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["positions"]


def create_order(instrument, entry_price, stop_loss_price, take_profit_price, units):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/orders"
    data = {
        "order": {
            "units": str(units),
            "instrument": instrument,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "price": str(round(stop_loss_price, 2))
            },
            "takeProfitOnFill": {
                "price": str(round(take_profit_price, 2))
            }
        }
    }
    log_to_firestore(f"📈 Création d'ordre OANDA DATA : {data, url}", level="OANDA")
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()


def close_order(instrument: str):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/positions/{instrument}/close"
    data = {
        "longUnits": "ALL",
        "shortUnits": "ALL"
    }
    response = requests.put(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()


def get_latest_price(instrument: str) -> float:
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/pricing"
    params = {"instruments": instrument}
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 401:
        raise Exception("❌ Unauthorized. Vérifie ton API Token et compte.")

    response.raise_for_status()
    data = response.json()
    prices = data.get("prices", [])
    if not prices:
        raise Exception(f"❌ Aucun prix retourné pour {instrument}")

    price = prices[0]
    try:
        bid = float(price["bids"][0]["price"])
        ask = float(price["asks"][0]["price"])
    except (KeyError, IndexError, ValueError) as e:
        raise Exception(f"⚠️ Extraction bid/ask échouée : {e}")

    return round((bid + ask) / 2, 2)


def list_instruments():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/instruments"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    raw = response.json()["instruments"]

    instruments = [
        {
            "name": inst["name"],
            "displayName": inst.get("displayName", ""),
            "type": inst.get("type", ""),
            "marginRate": inst.get("marginRate", "")
        }
        for inst in raw
    ]
    return instruments
