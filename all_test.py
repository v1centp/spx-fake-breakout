from app.strategies.sp500_mean_revert import process as mean_revert_process

# Range d'ouverture du 2025-08-12 : low=6395.17, high=6416.46
fake_bar = {
    "ev": "AM",
    "sym": "I:SPX",
    "op": 6421.80,             # open > high_15 (6416.46) -> setup SHORT
    "o":  6421.80,             # idem
    "c":  6410.25,             # close dans [6395.17, 6416.46]
    "h":  6422.10,             # dépasse nettement le high_15
    "l":  6402.30,
    "s":  1755007560000,       # 2025-08-12 14:06:00 UTC (≈ 10:06 NY)
    "e":  1755007620000,       # 2025-08-12 14:07:00 UTC
    "utc_time": "2025-08-12 14:06:00",  # dans la fenêtre 09:45–11:30 NY
    "day": "2025-08-12",
    "in_opening_range": False
}

try:
    mean_revert_process(fake_bar)
except Exception as e:
    print(f"❌ Erreur stratégie sp500_mean_revert : {e}")
