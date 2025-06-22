from app.services.strategy_logic import process_new_minute_bar

fake_bar = {
    "ev": "AM",
    "sym": "I:SPX",
    "op": 6005.0,
    "o": 6010.0,
    "c": 6008.5,              # ✅ clôture dans le range
    "h": 6022.0,              # ✅ dépasse high_15 (6018.2) de 3.8 pts
    "l": 6003.0,
    "s": 1750430580000,
    "e": 1750430640000,
    "utc_time": "2025-06-20 14:44:00",  # NY = 10:44, donc bien dans la fenêtre
    "day": "2025-06-20",
    "in_opening_range": False
}

process_new_minute_bar(fake_bar)
