# app/config/instrument_map.py

INSTRUMENT_MAP = {
    "OANDA:USDCHF": {
        "oanda": "USD_CHF",
        "decimals": 5,
        "step": 1,          # forex = unites entieres
        "base_currency": "CHF",
        "tp_ratio": 2.0,
    },
    # Extensible : ajouter d'autres paires ici
}


def resolve_instrument(tv_symbol: str) -> dict | None:
    return INSTRUMENT_MAP.get(tv_symbol)
