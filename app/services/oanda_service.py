# app/services/oanda_service.py

import os
import requests
from dotenv import load_dotenv

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
    return response.json()["account"]["balance"]
 
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

def create_order(instrument: str, units: int):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/orders"
    data = {
        "order": {
            "units": str(units),
            "instrument": instrument,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT"
        }
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()

def close_order(instrument: str):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/positions/{instrument}/close"
    data = { "longUnits": "ALL", "shortUnits": "ALL" }
    response = requests.put(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()

def get_latest_price(instrument: str):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/pricing"
    params = {"instruments": instrument}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    prices = response.json()["prices"]
    if not prices:
        raise Exception("No pricing data returned by OANDA.")
    # Use the "bids" and "asks" to compute mid-price
    bids = float(prices[0]["bids"][0]["price"])
    asks = float(prices[0]["asks"][0]["price"])
    return (bids + asks) / 2
