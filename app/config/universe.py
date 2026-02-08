# app/config/universe.py
UNIVERSE = {
    "I:SPX":  {"instrument": "SPX500_USD", "active": True,  "risk_chf": 50,
               "qty_step": 0.1, "sl_buffer": 3.0,
               "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "I:NDX":  {"instrument": "NAS100_USD", "active": True,  "risk_chf": 50,
               "qty_step": 0.1, "sl_buffer": 10.0,
               "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
}
