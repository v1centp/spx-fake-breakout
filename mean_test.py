# tests/run_mean_revert_e2e_1999.py
from datetime import datetime, timezone, timedelta
from app.services.firebase import get_firestore
from app.strategies.sp_mean_revert_multi import process as mean_revert
from app.config import universe as UNIV
from app.services import shared_strategy_tools as tools

DAY = "1999-01-01"  # demandé
db = get_firestore()

# ---------- 0) Activer stratégie & UNIVERSE ----------
def enable_everything():
    # Active la stratégie "mean_revert"
    db.collection("config").document("strategies").set({"mean_revert": True}, merge=True)

    # Active tous les symbols de l'UNIVERSE pour le test
    for sym in list(UNIV.UNIVERSE.keys()):
        UNIV.UNIVERSE[sym]["active"] = True

# ---------- 1) Seed opening ranges ----------
def seed_opening_ranges():
    ranges = {
        "AM.I:SPX": {"high": 1200.0,  "low": 1180.0},
        "AM.I:NDX": {"high": 2000.0,  "low": 1980.0},
        "AM.I:DJI": {"high": 11500.0, "low": 11400.0},
        "AM.I:RUT": {"high": 450.0,   "low": 440.0},
    }
    for sym, r in ranges.items():
        db.collection("opening_range").document(f"{DAY}_{sym}").set({
            "day": DAY, "symbol": sym,
            "high": float(r["high"]), "low": float(r["low"]),
            "range": float(r["high"] - r["low"]),
            "status": "ready", "count": 16
        })
    print("✅ opening_range seeded")

# ---------- 2) Mocks OANDA ----------
# Prix d'entrée = close de la bougie ; pas d'ordre réel
def mock_get_entry_price(instrument: str):
    # on récupère le dernier close qu'on a utilisé via variable globale (mise à jour dans run)
    return _LAST_CLOSE[0]

def mock_execute_trade(instrument, entry_price, sl_price, tp_price, units, direction):
    # Simule l'exécution et renvoie les units signées comme le vrai helper
    return -units if direction == "SHORT" else units

# Remplace les helpers par les mocks
tools.get_entry_price = mock_get_entry_price
tools.execute_trade = mock_execute_trade

# ---------- 3) Helpers bougies ----------
def utc_ms(dt_utc: datetime) -> int:
    return int(dt_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)

def make_bar(sym: str, dt_utc_end: datetime, o, c, h, l, op=None, in_opening_range=False):
    e = utc_ms(dt_utc_end); s = e - 60_000
    return {
        "ev": "AM", "sym": sym,
        "op": float(op if op is not None else o),
        "o": float(o), "c": float(c), "h": float(h), "l": float(l),
        "s": s, "e": e,
        "utc_time": dt_utc_end.strftime("%Y-%m-%d %H:%M:%S"),
        "day": DAY, "in_opening_range": bool(in_opening_range),
    }

def write_bar_to_firestore(bar: dict):
    doc_id = f"{bar['sym']}_{bar['e']}"
    db.collection("ohlc_1m").document(doc_id).set(bar)

# ---------- 4) Jeu de bougies ----------
# 1999-01-01 = EST (UTC-5) → 10:51 NY = 15:51 UTC
t_1051 = datetime(1999, 1, 1, 15, 51, 0, tzinfo=timezone.utc)
t_1057 = datetime(1999, 1, 1, 15, 57, 0, tzinfo=timezone.utc)
t_1103 = datetime(1999, 1, 1, 16, 3, 0, tzinfo=timezone.utc)
t_1109 = datetime(1999, 1, 1, 16, 9, 0, tzinfo=timezone.utc)
t_1111 = datetime(1999, 1, 1, 16, 11, 0, tzinfo=timezone.utc)

TEST_BARS = [
    # SPX (range 1180-1200)
    make_bar("AM.I:SPX", t_1051, o=1205.0, c=1195.0, h=1206.0, l=1193.0),  # SHORT
    make_bar("AM.I:SPX", t_1057, o=1175.0, c=1186.0, h=1187.0, l=1174.0),  # LONG
    make_bar("AM.I:SPX", t_1103, o=1189.0, c=1190.0, h=1192.0, l=1187.0),  # REJECT

    # NDX (range 1980-2000)
    make_bar("AM.I:NDX", t_1109, o=2005.0, c=1992.0, h=2010.0, l=1990.0),  # SHORT
    make_bar("AM.I:NDX", t_1111, o=1975.0, c=1988.0, h=1990.0, l=1972.0),  # LONG
    make_bar("AM.I:NDX", t_1103 + timedelta(minutes=2), o=1988.0, c=1990.0, h=1993.0, l=1986.0),  # REJECT

    # DJI (range 11400-11500)
    make_bar("AM.I:DJI", t_1051 + timedelta(minutes=1), o=11520.0, c=11480.0, h=11540.0, l=11470.0),  # SHORT
    make_bar("AM.I:DJI", t_1057 + timedelta(minutes=1), o=11390.0, c=11430.0, h=11440.0, l=11380.0),  # LONG
    make_bar("AM.I:DJI", t_1103 + timedelta(minutes=4), o=11430.0, c=11460.0, h=11470.0, l=11420.0),  # REJECT

    # RUT (range 440-450)
    make_bar("AM.I:RUT", t_1109 + timedelta(minutes=2), o=451.5, c=446.0, h=452.0, l=445.5),  # SHORT
    make_bar("AM.I:RUT", t_1111 + timedelta(minutes=2), o=438.0, c=444.0, h=444.5, l=437.5),  # LONG
    make_bar("AM.I:RUT", t_1103 + timedelta(minutes=6), o=446.0, c=447.0, h=447.5, l=445.0),  # REJECT
]

# ---------- 5) Run ----------
_LAST_CLOSE = [0.0]  # storage mut pour le mock get_entry_price

def run():
    enable_everything()
    seed_opening_ranges()

    # Vide les trades de la journée (facultatif, pour rerun propre)
    sym_ref = db.collection("trading_days").document(DAY).collection("symbols").stream()
    for s in sym_ref:
        # Danger: supprime juste la sous-collection 'trades'
        for tr in db.collection("trading_days").document(DAY).collection("symbols").document(s.id).collection("trades").stream():
            tr.reference.delete()

    # Écrit chaque bougie dans ohlc_1m puis lance la stratégie
    for bar in TEST_BARS:
        write_bar_to_firestore(bar)
        _LAST_CLOSE[0] = bar["c"]   # utilisé par le mock prix
        mean_revert(bar)

    print("✅ mean_revert exécuté")

    # Petit récap trades
    for sym in ["AM.I:SPX","AM.I:NDX","AM.I:DJI","AM.I:RUT"]:
        trades = list(
            db.collection("trading_days").document(DAY)
              .collection("symbols").document(sym)
              .collection("trades").stream()
        )
        print(f"{sym}: {len(trades)} trades")
        for t in trades:
            print("  ->", t.to_dict())

if __name__ == "__main__":
    run()
