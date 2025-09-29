# app/config/universe.py
UNIVERSE = {
    # Polygon      → OANDA instrument
    "AM.I:SPX":   {"instrument": "SPX500_USD", "active": True,  "risk_chf": 50},
    "AM.I:NDX":   {"instrument": "NAS100_USD", "active": True,  "risk_chf": 50},
    "AM.I:DJI":   {"instrument": "US30_USD",   "active": False, "risk_chf": 50},
    "AM.I:RUT":   {"instrument": "US2000_USD", "active": False, "risk_chf": 50},
    # ajoute/active à la demande (ex: EU indices → GER40_EUR, etc.)
}
