import os
from dotenv import load_dotenv

# Charge les variables d'environnement du fichier .env
load_dotenv()

from app.strategies.gpt_trader import process as gpt_process

fake_bar = {
    "ev": "AM",
    "sym": "I:SPX",
    "op": 6005.0,
    "o": 6010.0,
    "c": 6008.5,
    "h": 6022.0,
    "l": 6003.0,
    "s": 1750430580000,
    "e": 1750430640000,
    "utc_time": "2025-06-25 14:44:00",
    "day": "2025-06-26",
    "in_opening_range": False
}

gpt_process(fake_bar)
