# app/config/instrument_map.py

# SL buffer: 2 pips (0.00020 for 5-decimal, 0.020 for 3-decimal)
# TP ratio: 6R

INSTRUMENT_MAP = {
    "OANDA:USDCHF": {
        "oanda": "USD_CHF",
        "decimals": 5,
        "step": 1,
        "base_currency": "CHF",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
    "OANDA:EURUSD": {
        "oanda": "EUR_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
    "OANDA:USDJPY": {
        "oanda": "USD_JPY",
        "decimals": 3,
        "step": 1,
        "base_currency": "JPY",
        "tp_ratio": 6.0,
        "sl_buffer": 0.020,
    },
    "OANDA:GBPUSD": {
        "oanda": "GBP_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
    "OANDA:EURGBP": {
        "oanda": "EUR_GBP",
        "decimals": 5,
        "step": 1,
        "base_currency": "GBP",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
    "OANDA:EURJPY": {
        "oanda": "EUR_JPY",
        "decimals": 3,
        "step": 1,
        "base_currency": "JPY",
        "tp_ratio": 6.0,
        "sl_buffer": 0.020,
    },
    "OANDA:GBPJPY": {
        "oanda": "GBP_JPY",
        "decimals": 3,
        "step": 1,
        "base_currency": "JPY",
        "tp_ratio": 6.0,
        "sl_buffer": 0.020,
    },
    "OANDA:AUDUSD": {
        "oanda": "AUD_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
    "OANDA:NZDUSD": {
        "oanda": "NZD_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
    "OANDA:USDCAD": {
        "oanda": "USD_CAD",
        "decimals": 5,
        "step": 1,
        "base_currency": "CAD",
        "tp_ratio": 6.0,
        "sl_buffer": 0.00020,
    },
}


def resolve_instrument(tv_symbol: str) -> dict | None:
    return INSTRUMENT_MAP.get(tv_symbol)
