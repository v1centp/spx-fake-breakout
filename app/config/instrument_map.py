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
    # Extensible : ajouter d'autres paires ici
}


def resolve_instrument(tv_symbol: str) -> dict | None:
    return INSTRUMENT_MAP.get(tv_symbol)
