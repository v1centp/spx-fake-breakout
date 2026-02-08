import threading
import time
from datetime import datetime
from app.services.firebase import get_firestore
from app.services import oanda_service
from app.services.log_service import log_to_firestore

POLL_INTERVAL = 30  # seconds

_open_trades = []  # list of (doc_ref, oanda_trade_id)


def _load_open_trades():
    """Load all trades with outcome == 'open' from Firestore."""
    db = get_firestore()
    trades = []

    # Classic strategies: collection_group("trades")
    for doc in db.collection_group("trades").where("outcome", "==", "open").stream():
        oanda_id = doc.to_dict().get("oanda_trade_id")
        if oanda_id:
            trades.append((doc.reference, oanda_id))

    # GPT trader: collection_group("executions")
    for doc in db.collection_group("executions").where("outcome", "==", "open").stream():
        oanda_id = doc.to_dict().get("oanda_trade_id")
        if oanda_id:
            trades.append((doc.reference, oanda_id))

    return trades


def _determine_outcome(realized_pl: float) -> str:
    if realized_pl > 0:
        return "win"
    elif realized_pl < 0:
        return "loss"
    return "breakeven"


def _poll_loop():
    global _open_trades

    while True:
        try:
            # Reload open trades each cycle to pick up new ones
            _open_trades = _load_open_trades()

            if _open_trades:
                log_to_firestore(
                    f"[TradeTracker] Tracking {len(_open_trades)} open trade(s)",
                    level="INFO"
                )

            still_open = []
            for doc_ref, oanda_trade_id in _open_trades:
                try:
                    details = oanda_service.get_trade_details(oanda_trade_id)
                except Exception as e:
                    log_to_firestore(
                        f"[TradeTracker] Error fetching trade {oanda_trade_id}: {e}",
                        level="ERROR"
                    )
                    still_open.append((doc_ref, oanda_trade_id))
                    continue

                if details["state"] == "CLOSED":
                    realized_pl = float(details["realizedPL"])
                    outcome = _determine_outcome(realized_pl)

                    doc_ref.update({
                        "outcome": outcome,
                        "realized_pnl": realized_pl,
                        "close_time": datetime.now().isoformat(),
                    })

                    log_to_firestore(
                        f"[TradeTracker] Trade {oanda_trade_id} closed: {outcome} (PnL: {realized_pl})",
                        level="TRADING"
                    )
                else:
                    still_open.append((doc_ref, oanda_trade_id))

            _open_trades = still_open

        except Exception as e:
            log_to_firestore(f"[TradeTracker] Poll error: {e}", level="ERROR")

        time.sleep(POLL_INTERVAL)


def start():
    thread = threading.Thread(target=_poll_loop, daemon=True)
    thread.start()
    log_to_firestore("[TradeTracker] Background tracker started", level="INFO")
