# app/config/instrument_map.py

INSTRUMENT_MAP = {
    "OANDA:USDCHF": {
        "oanda": "USD_CHF",
        "decimals": 5,
        "step": 1,          # forex = unites entieres
        "base_currency": "CHF",
        "tp_ratio": 3.0,
        "sl_buffer": 0.00030,  # 3 pips de buffer au-dela du Kijun
    },
    "OANDA:EURUSD": {
        "oanda": "EUR_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
    },
    "OANDA:USDJPY": {
        "oanda": "USD_JPY",
        "decimals": 3,
        "step": 1,
        "base_currency": "JPY",
    },
    "OANDA:GBPUSD": {
        "oanda": "GBP_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
    },
    "OANDA:EURGBP": {
        "oanda": "EUR_GBP",
        "decimals": 5,
        "step": 1,
        "base_currency": "GBP",
    },
    "OANDA:EURJPY": {
        "oanda": "EUR_JPY",
        "decimals": 3,
        "step": 1,
        "base_currency": "JPY",
    },
    "OANDA:GBPJPY": {
        "oanda": "GBP_JPY",
        "decimals": 3,
        "step": 1,
        "base_currency": "JPY",
    },
    "OANDA:AUDUSD": {
        "oanda": "AUD_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
    },
    "OANDA:NZDUSD": {
        "oanda": "NZD_USD",
        "decimals": 5,
        "step": 1,
        "base_currency": "USD",
    },
    "OANDA:USDCAD": {
        "oanda": "USD_CAD",
        "decimals": 5,
        "step": 1,
        "base_currency": "CAD",
    },
}


def resolve_instrument(tv_symbol: str) -> dict | None:
    return INSTRUMENT_MAP.get(tv_symbol)
