# app/config/universe.py
UNIVERSE = {
    # --- US (déjà) ---
    "AM.I:SPX": {"instrument": "SPX500_USD", "active": True, "risk_chf": 50,
                 "qty_step": 0.1,
                 "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "AM.I:NDX": {"instrument": "NAS100_USD", "active": True, "risk_chf": 50,
                 "qty_step": 0.1,
                 "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "AM.I:DJI": {"instrument": "US30_USD", "active": True, "risk_chf": 50,
                 "qty_step": 0.1,
                 "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},
    "AM.I:RUT": {"instrument": "US2000_USD", "active": True, "risk_chf": 50,
                 "qty_step": 0.1,
                 "session": {"tz": "America/New_York", "open": "09:30", "or_minutes": 15, "trade_end": "11:30"}},

    # --- Europe (exemples) ---
    # ⚠️ Renseigne exactement les noms d’instrument OANDA retournés par list_instruments()
    # (souvent: EU50_EUR, DE40_EUR, FR40_EUR, UK100_GBP, CH20_CHF)
    "AM.I:EU50": {"instrument": "EU50_EUR", "active": True, "risk_chf": 50,
                  "qty_step": 0.1,
                  "session": {"tz": "Europe/Paris",  "open": "09:00", "or_minutes": 15, "trade_end": "12:00"}},
    "AM.I:FR40": {"instrument": "FR40_EUR", "active": True, "risk_chf": 50,
                  "qty_step": 0.1,
                  "session": {"tz": "Europe/Paris",  "open": "09:00", "or_minutes": 15, "trade_end": "12:00"}},
    "AM.I:UK100": {"instrument": "UK100_GBP", "active": True, "risk_chf": 50,
                   "qty_step": 0.1,
                   "session": {"tz": "Europe/London","open": "08:00", "or_minutes": 15, "trade_end": "11:00"}},
    "AM.I:CH20": {"instrument": "CH20_CHF", "active": True, "risk_chf": 50,
                  "qty_step": 0.1,
                  "session": {"tz": "Europe/Zurich","open": "09:00", "or_minutes": 15, "trade_end": "12:00"}},
    # ajoute DE40_EUR etc. si tu veux
}
