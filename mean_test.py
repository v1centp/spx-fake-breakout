# tests/run_mean_revert_live_oanda.py
from datetime import datetime, timedelta, timezone
import pytz

from app.services.firebase import get_firestore
import app.strategies.sp_mean_revert_multi as strat   # on n'altère rien
from app.config.universe import UNIVERSE
from app.services import oanda_service

DAY = "1999-01-01"   # laissé volontairement (la stratégie se base sur la bougie, pas la date réelle)
db = get_firestore()

# ---------- helpers sessions ----------
def _parse_hhmm(s: str):
    h, m = s.split(":"); return int(h), int(m)

def _session_for(sym: str):
    s = UNIVERSE.get(sym, {}).get("session", {})
    tz = pytz.timezone(s.get("tz", "America/New_York"))
    oh, om = _parse_hhmm(s.get("open", "09:30"))
    or_min = int(s.get("or_minutes", 15))
    th, tm = _parse_hhmm(s.get("trade_end", "11:30"))
    return tz, oh, om, or_min, th, tm

def _local_dt(day: str, tzname: str, hh: int, mm: int):
    tz = pytz.timezone(tzname)
    local_naive = datetime.strptime(f"{day} {hh:02d}:{mm:02d}:00", "%Y-%m-%d %H:%M:%S")
    return tz.localize(local_naive)

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

def write_bar(bar: dict):
    db.collection("ohlc_1m").document(f"{bar['sym']}_{bar['e']}").set(bar)

# ---------- enable strat + seed ranges ----------
def enable_everything():
    db.collection("config").document("strategies").set({"mean_revert": True}, merge=True)
    for sym in UNIVERSE: UNIVERSE[sym]["active"] = True

RANGE_OVERRIDES = {
    # US overrides (les autres symboles auront un range générique 1000–1010)
    "AM.I:SPX": (1180.0, 1200.0),
    "AM.I:NDX": (1980.0, 2000.0),
    "AM.I:DJI": (11400.0, 11500.0),
    "AM.I:RUT": (440.0, 450.0),
}

def seed_opening_ranges():
    for sym in UNIVERSE.keys():
        lo, hi = RANGE_OVERRIDES.get(sym, (1000.0, 1010.0))
        db.collection("opening_range").document(f"{DAY}_{sym}").set({
            "day": DAY, "symbol": sym,
            "high": float(hi), "low": float(lo),
            "range": float(hi - lo), "status": "ready", "count": 16
        })

# ---------- ancrage SL pour viser ~0.2 lot ----------
def seed_anchors(sym: str, open_end_utc: datetime, lo: float, hi: float):
    """
    Crée 2 bougies 'ancres' quelques minutes AVANT les signaux pour fixer:
      - max(high) du jour ≈ c*(1+delta)    (pour SHORT)
      - min(low)  du jour ≈ c*(1-delta)    (pour LONG)
    delta est calibré avec le PRIX OANDA en live pour viser units ≈ 0.2
    """
    instrument = UNIVERSE[sym]["instrument"]
    entry = oanda_service.get_latest_price(instrument)   # ← prix OANDA live
    target_units = 0.2
    # risk = entry * delta  => delta = risk_limit / (entry * target_units)
    risk_limit = UNIVERSE[sym].get("risk_chf", 50)
    delta = min(0.15, max(0.002, risk_limit / (entry * target_units)))  # borne [0.2%, 15%] par sécurité

    mid = (lo + hi) / 2.0
    # ancre HIGH (pour SHORT)
    high_anchor = mid * (1.0 + delta)
    # ancre LOW (pour LONG)
    low_anchor  = mid * (1.0 - delta)

    # deux bougies avant la fenêtre de test
    a1_end = open_end_utc - timedelta(minutes=2)
    a2_end = open_end_utc - timedelta(minutes=1)

    bar_high = make_bar(sym, a1_end, o=mid, c=mid, h=high_anchor, l=mid*0.999)
    bar_low  = make_bar(sym, a2_end, o=mid, c=mid, h=mid*1.001,   l=low_anchor)

    write_bar(bar_high)
    write_bar(bar_low)

# ---------- génération des 3 bougies de test par symbole ----------
def bars_for_symbol(sym: str, lo: float, hi: float, open_end_utc: datetime):
    r = hi - lo
    # SHORT: open > high, close revient dans range
    b_short = make_bar(sym, open_end_utc + timedelta(minutes=1),
                       o=hi + 0.05 * r, c=hi - 0.25 * r, h=hi + 0.06 * r, l=hi - 0.30 * r, op=hi + 0.05 * r)
    # LONG: open < low, close revient dans range
    b_long  = make_bar(sym, open_end_utc + timedelta(minutes=7),
                       o=lo - 0.05 * r, c=lo + 0.25 * r, h=lo + 0.30 * r, l=lo - 0.06 * r, op=lo - 0.05 * r)
    # REJECT: open & close dans range
    mid = lo + 0.5 * r
    b_reject = make_bar(sym, open_end_utc + timedelta(minutes=13),
                        o=mid, c=mid + 0.05 * r, h=mid + 0.10 * r, l=mid - 0.05 * r, op=mid)
    return [b_short, b_long, b_reject]

# ---------- run ----------
def run():
    print("⚠️ TEST LIVE: des ordres OANDA vont être envoyés pour chaque symbole ACTIF.")
    enable_everything()
    seed_opening_ranges()

    for sym, cfg in UNIVERSE.items():
        if not cfg.get("active"): 
            continue

        tz, oh, om, or_min, th, tm = _session_for(sym)
        open_end_local = _local_dt(DAY, tz.zone, oh, om) + timedelta(minutes=or_min)
        open_end_utc = open_end_local.astimezone(timezone.utc)

        lo, hi = RANGE_OVERRIDES.get(sym, (1000.0, 1010.0))

        # 1) ancrer les extrêmes du jour pour calibrer le SL (et donc la taille)
        seed_anchors(sym, open_end_utc, lo, hi)

        # 2) écrire et traiter les 3 bougies
        for bar in bars_for_symbol(sym, lo, hi, open_end_utc):
            write_bar(bar)
            strat.process(bar)  # ← appelle OANDA via ta stratégie

    print("✅ mean_revert exécuté (LIVE OANDA)")

    # récap rapide
    for sym in UNIVERSE.keys():
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
