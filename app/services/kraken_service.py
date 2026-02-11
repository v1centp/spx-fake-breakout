# app/services/kraken_service.py
import os
import time
import hashlib
import hmac
import base64
import urllib.parse
import requests
from dotenv import load_dotenv
from app.services.log_service import log_to_firestore

load_dotenv()

KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
KRAKEN_BASE_URL = "https://api.kraken.com"

DECIMALS_BY_PAIR = {
    "XBTUSD": 1,
    "ETHUSD": 2,
    "SOLUSD": 2,
    "ADAUSD": 6,
    "XDGUSD": 5,
    "XXRPZUSD": 5,
    "LINKUSD": 3,
    "XXMRZUSD": 2,
    "PEPEUSD": 9,
    "ATOMUSD": 4,
}


def format_price(price: float, pair: str) -> str:
    decimals = DECIMALS_BY_PAIR.get(pair, 2)
    return f"{round(price, decimals):.{decimals}f}"


def _nonce():
    return str(int(time.time() * 1000))


def _sign(urlpath: str, data: dict) -> dict:
    """Generate Kraken API-Sign header (HMAC-SHA512)."""
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(KRAKEN_API_SECRET), message, hashlib.sha512)
    return {
        "API-Key": KRAKEN_API_KEY,
        "API-Sign": base64.b64encode(mac.digest()).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _private_request(endpoint: str, data: dict = None) -> dict:
    """Authenticated POST to Kraken private API."""
    urlpath = f"/0/private/{endpoint}"
    url = KRAKEN_BASE_URL + urlpath
    if data is None:
        data = {}
    data["nonce"] = _nonce()
    headers = _sign(urlpath, data)
    response = requests.post(url, headers=headers, data=urllib.parse.urlencode(data))
    response.raise_for_status()
    result = response.json()
    if result.get("error") and len(result["error"]) > 0:
        raise Exception(f"Kraken API error: {result['error']}")
    return result.get("result", {})


def _public_request(endpoint: str, params: dict = None) -> dict:
    """Public GET to Kraken API."""
    url = f"{KRAKEN_BASE_URL}/0/public/{endpoint}"
    response = requests.get(url, params=params or {})
    response.raise_for_status()
    result = response.json()
    if result.get("error") and len(result["error"]) > 0:
        raise Exception(f"Kraken API error: {result['error']}")
    return result.get("result", {})


def get_account_balance() -> float:
    """Get USD balance from Kraken account."""
    result = _private_request("Balance")
    # Kraken uses ZUSD for USD
    return float(result.get("ZUSD", 0))


def get_latest_price(pair: str) -> float:
    """Get mid price (ask+bid)/2 for a pair."""
    result = _public_request("Ticker", {"pair": pair})
    # Kraken returns data keyed by internal pair name, take first entry
    ticker = next(iter(result.values()))
    ask = float(ticker["a"][0])
    bid = float(ticker["b"][0])
    decimals = DECIMALS_BY_PAIR.get(pair, 2)
    return round((ask + bid) / 2, decimals)


def create_order(pair: str, sl_price: float, tp_price: float, volume: float, side: str, validate: bool = False) -> dict:
    """
    Create a market order with SL as conditional close.
    TP is placed as a separate limit order.
    Returns dict with txid list and order description.
    """
    data = {
        "pair": pair,
        "type": side.lower(),  # "buy" or "sell"
        "ordertype": "market",
        "volume": str(abs(volume)),
        "close[ordertype]": "stop-loss",
        "close[price]": format_price(sl_price, pair),
    }
    if validate:
        data["validate"] = "true"

    log_to_firestore(f"Kraken create_order: {data}", level="KRAKEN")
    result = _private_request("AddOrder", data)
    txid_list = result.get("txid", [])
    main_txid = txid_list[0] if txid_list else None

    # Place TP as separate limit order (opposite side)
    tp_txid = None
    if main_txid and not validate:
        tp_side = "sell" if side.lower() == "buy" else "buy"
        tp_data = {
            "pair": pair,
            "type": tp_side,
            "ordertype": "take-profit",
            "price": format_price(tp_price, pair),
            "volume": str(abs(volume)),
        }
        try:
            tp_result = _private_request("AddOrder", tp_data)
            tp_txid_list = tp_result.get("txid", [])
            tp_txid = tp_txid_list[0] if tp_txid_list else None
        except Exception as e:
            log_to_firestore(f"Kraken TP order failed: {e}", level="ERROR")

    return {
        "txid": main_txid,
        "tp_txid": tp_txid,
        "descr": result.get("descr", {}),
    }


def cancel_order(txid: str) -> dict:
    """Cancel an open order by txid."""
    return _private_request("CancelOrder", {"txid": txid})


def close_trade(txid: str, pair: str, side: str, volume: float = None) -> dict:
    """
    Close a trade by placing an opposite market order.
    side = original trade side ("buy" or "sell").
    """
    close_side = "sell" if side.lower() == "buy" else "buy"
    data = {
        "pair": pair,
        "type": close_side,
        "ordertype": "market",
    }
    if volume is not None:
        data["volume"] = str(abs(volume))
    else:
        # Query the original order to get remaining volume
        order_info = get_order_status(txid)
        data["volume"] = order_info.get("vol_exec", "0")

    log_to_firestore(f"Kraken close_trade: {data}", level="KRAKEN")
    return _private_request("AddOrder", data)


def modify_trade_sl(txid: str, new_sl: float, pair: str) -> dict:
    """Edit the conditional close SL price on an existing order."""
    data = {
        "txid": txid,
        "pair": pair,
        "price2": format_price(new_sl, pair),
    }
    try:
        return _private_request("EditOrder", data)
    except Exception as e:
        log_to_firestore(f"Kraken modify SL error (txid={txid}): {e}", level="ERROR")
        raise


def get_order_status(txid: str) -> dict:
    """Query order info by txid."""
    result = _private_request("QueryOrders", {"txid": txid})
    order = result.get(txid, {})
    return {
        "txid": txid,
        "status": order.get("status", "unknown"),
        "vol": order.get("vol", "0"),
        "vol_exec": order.get("vol_exec", "0"),
        "price": order.get("price", "0"),
        "cost": order.get("cost", "0"),
        "descr": order.get("descr", {}),
        "opentm": order.get("opentm"),
        "closetm": order.get("closetm"),
    }


def get_trade_details(txid: str) -> dict:
    """Get trade/order details, mapping to a format compatible with the trade tracker."""
    order = get_order_status(txid)
    status = order.get("status", "unknown")

    # Map Kraken statuses to our expected format
    # Kraken: pending, open, closed, canceled, expired
    if status in ("closed", "canceled", "expired"):
        state = "CLOSED"
    else:
        state = "OPEN"

    avg_price = order.get("price", "0")
    vol_exec = order.get("vol_exec", "0")

    return {
        "id": txid,
        "state": state,
        "realizedPL": "0",  # Kraken doesn't provide PnL directly — computed externally
        "unrealizedPL": "0",
        "price": avg_price,
        "currentUnits": vol_exec,
        "averageClosePrice": avg_price if state == "CLOSED" else None,
        "sl_filled": False,
        "tp_filled": False,
    }
