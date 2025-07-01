from app.strategies.fake_breakout_strict import process as strict_process
from app.strategies import get_all_strategies


fake_bar = {
    "ev": "AM",
    "sym": "I:SPX",
    "op": 6193.360000000001,
    "o": 6185.38,
    "c": 6186.76,              # ✅ clôture dans le range
    "h": 6186.76,              # ✅ dépasse high_15 (ex: 6018.2)
    "l": 6185.38,
    "s": 1751295900000,
    "e": 1751295960000,
    "utc_time": "2025-06-30 15:06:00",  # NY = 10:44
    "day": "2025-06-30",
    "in_opening_range": False
}

for strategy_fn in get_all_strategies():
                    try:
                        strategy_fn(fake_bar)
                    except Exception as e:
                        print(f"❌ Erreur stratégie {strategy_fn.__name__} : {e}")
                        
                    

