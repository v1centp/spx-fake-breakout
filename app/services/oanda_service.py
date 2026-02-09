import os
import requests
from dotenv import load_dotenv
from app.services.log_service import log_to_firestore

# 📦 Chargement des variables d'environnement
load_dotenv()

OANDA_API_URL = os.getenv("OANDA_API_URL")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

headers = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type": "application/json"
}

# 🎯 Précision maximale par instrument
DECIMALS_BY_INSTRUMENT = {
    "SPX500_USD": 1,
    "NAS100_USD": 1,
    "US30_USD": 1,
    "EUR_USD": 5,
    "USD_JPY": 3,
    "USD_CHF": 5,
    "GBP_USD": 5,
    "EUR_GBP": 5,
    "EUR_JPY": 3,
    "GBP_JPY": 3,
    "AUD_USD": 5,
    "NZD_USD": 5,
    "USD_CAD": 5,
}

def format_price(price: float, instrument: str) -> str:
    decimals = DECIMALS_BY_INSTRUMENT.get(instrument, 2)
    return f"{round(price, decimals):.{decimals}f}"

# ✅ Obtenir le solde du compte
def get_account_balance():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/summary"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return float(response.json()["account"]["balance"])

# ✅ Obtenir les trades ouverts
def get_open_trades():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/openTrades"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get("trades", [])

# ✅ Obtenir les positions ouvertes
def get_open_positions():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/openPositions"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["positions"]

# ✅ Créer un ordre MARKET avec SL et TP
def create_order(instrument, entry_price, stop_loss_price, take_profit_price, units):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/orders"

    qty = round(float(units), 1)              # ex: 0.73 -> 0.7 ; 1.25 -> 1.2 (si tu veux CEIL, dis-le)
    units_str = f"{qty:.1f}"
    if units_str in ("0.0", "-0.0"):
        raise ValueError(f"units too small: {units}")

    data = {
        "order": {
            "units": units_str,  # << string décimale, pas int !
            "instrument": instrument,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill":   {"price": format_price(stop_loss_price, instrument)},
            "takeProfitOnFill": {"price": format_price(take_profit_price, instrument)}
        }
    }

    log_to_firestore(f"📈 Création d'ordre OANDA DATA : {data, url}", level="OANDA")
    response = requests.post(url, headers=headers, json=data)
    if not response.ok:
        log_to_firestore(f"❌ Erreur OANDA : {response.status_code} — {response.text}", level="ERROR")
    response.raise_for_status()
    return response.json()

# ✅ Fermer toutes les positions pour un instrument donné
def close_order(instrument: str):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/positions/{instrument}/close"
    data = {
        "longUnits": "ALL",
        "shortUnits": "ALL"
    }
    response = requests.put(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json()

# ✅ Obtenir le dernier prix moyen (bid + ask) / 2
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

    decimals = DECIMALS_BY_INSTRUMENT.get(instrument, 5)
    return round((bid + ask) / 2, decimals)

# ✅ Lister tous les instruments disponibles sur le compte
def list_instruments():
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/instruments"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    raw = response.json()["instruments"]

    return [
        {
            "name": inst["name"],
            "displayName": inst.get("displayName", ""),
            "type": inst.get("type", ""),
            "marginRate": inst.get("marginRate", "")
        }
        for inst in raw
    ]

def close_trade(trade_id: str, units=None):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close"
    kwargs = {"headers": headers}
    if units is not None:
        kwargs["json"] = {"units": str(abs(float(units)))}
    response = requests.put(url, **kwargs)
    if not response.ok:
        log_to_firestore(
            f"Erreur OANDA close trade {trade_id}: {response.status_code} — {response.text}",
            level="ERROR"
        )
    response.raise_for_status()
    return response.json()


def modify_trade_sl(trade_id: str, new_sl_price: float, instrument: str):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/orders"
    data = {"stopLoss": {"price": format_price(new_sl_price, instrument)}}
    response = requests.put(url, headers=headers, json=data)
    if not response.ok:
        log_to_firestore(
            f"❌ Erreur OANDA modify SL trade {trade_id}: {response.status_code} — {response.text}",
            level="ERROR"
        )
    response.raise_for_status()
    return response.json()


def get_trade_details(trade_id: str):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    trade = response.json()["trade"]
    return {
        "id": trade["id"],
        "instrument": trade["instrument"],
        "state": trade["state"],
        "realizedPL": trade.get("realizedPL", "0"),
        "unrealizedPL": trade.get("unrealizedPL", "0"),
        "price": trade.get("price", "0"),
        "currentUnits": trade.get("currentUnits", "0"),
    }

def get_closed_trades(count: int = 500):
    url = f"{OANDA_API_URL}/accounts/{OANDA_ACCOUNT_ID}/trades"
    params = {"state": "CLOSED", "count": count}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json().get("trades", [])

def get_candles(instrument: str, from_time: str, to_time: str, granularity: str = "M1") -> list:
    url = f"{OANDA_API_URL}/instruments/{instrument}/candles"
    params = {
        "granularity": granularity,
        "from": from_time,
        "to": to_time,
        "price": "M",
    }
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    raw = response.json().get("candles", [])
    return [
        {
            "time": c["time"],
            "o": float(c["mid"]["o"]),
            "h": float(c["mid"]["h"]),
            "l": float(c["mid"]["l"]),
            "c": float(c["mid"]["c"]),
            "complete": c.get("complete", False),
        }
        for c in raw
    ]
