# app/config/universe.py
UNIVERSE = {
    "I:SPX":  {"instrument": "SPX500_USD", "active": True,  "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "I:NDX":  {"instrument": "NAS100_USD", "active": True,  "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "I:DJI":  {"instrument": "US30_USD",   "active": True,  "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "I:RUT":  {"instrument": "US2000_USD", "active": True,  "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},

    # Europe (exemples)
    "I:MSG50EG": {"instrument": "EU50_EUR",   "active": False, "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "Europe/Paris", "open": "09:00", "or_minutes": 15, "trade_end": "12:00"}}
}
