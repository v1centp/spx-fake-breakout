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
    "I:EU50": {"instrument": "EU50_EUR",   "active": False, "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "Europe/Paris", "open": "09:00", "or_minutes": 15, "trade_end": "12:00"}},
    "I:FR40": {"instrument": "FR40_EUR",   "active": False, "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "Europe/Paris", "open": "09:00", "or_minutes": 15, "trade_end": "12:00"}},
    "I:UK100":{"instrument": "UK100_GBP",  "active": False, "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "Europe/London","open": "08:00", "or_minutes": 15, "trade_end": "11:00"}},
    "I:CH20": {"instrument": "CH20_CHF",   "active": False, "risk_chf": 50,
               "qty_step": 0.1,
               "session": {"tz": "Europe/Zurich","open": "09:00", "or_minutes": 15, "trade_end": "12:00"}},
}
