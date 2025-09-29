# app/services/shared_strategy_tools.py
import math
from app.services import oanda_service

STEP = 0.1  # pas OANDA

def _floor_step(x: float, step: float = STEP) -> float:
    """Arrondit vers le bas au multiple de `step` (ex: 0.37 -> 0.3)."""
    if step <= 0:
        return 0.0
    return math.floor(x / step) * step

def get_entry_price(instrument: str):
    return oanda_service.get_latest_price(instrument)

def calculate_sl_tp(entry, sl_level, direction):
    risk = abs(entry - sl_level)
    if risk == 0:
        return None, None, 0
    tp = entry + 1.75 * risk if direction == "LONG" else entry - 1.75 * risk
    return round(sl_level, 2), round(tp, 2), risk

def compute_position_size(risk_per_unit, risk_limit=50):
    """Taille théorique puis **floor** au pas 0.1 (pour respecter le risque max)."""
    if risk_per_unit <= 0:
        return 0.0
    raw = risk_limit / risk_per_unit
    return _floor_step(raw)  # ex: 0.37 -> 0.3 ; 1.26 -> 1.2

def execute_trade(instrument: str, entry_price, sl_price, tp_price, units, direction):
    """
    Envoie un multiple **exact** de 0.1 :
    - on normalise la quantité au pas 0.1
    - on applique le signe selon la direction
    """
    qty = _floor_step(abs(float(units)))      # sécurise au pas 0.1
    print(f"execute_trade: {units} -> {qty} ({direction})")
    if qty < STEP:
        raise ValueError(f"units too small (< {STEP}): {units}")
    signed = -qty if direction == "SHORT" else qty

    oanda_service.create_order(
        instrument=instrument,
        entry_price=entry_price,
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
        units=signed
    )
    return signed
