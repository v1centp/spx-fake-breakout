from fastapi import APIRouter, Query
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/trades")
def get_all_trades():
    db = get_firestore()

    trades_snap = db.collection_group("trades").stream()
    trades = []
    for doc in trades_snap:
        data = doc.to_dict()
        if not data.get("entry"):
            continue
        trades.append(data | {"id": doc.id, "doc_path": doc.reference.path})

    # Include GPT rejections
    rejections_snap = (
        db.collection("strategies").document("ichimoku")
          .collection("gpt_rejections").stream()
    )
    for doc in rejections_snap:
        data = doc.to_dict()
        trades.append({
            "id": doc.id,
            "doc_path": doc.reference.path,
            "strategy": "ichimoku",
            "instrument": data.get("instrument"),
            "direction": data.get("signal_direction"),
            "timestamp": data.get("timestamp"),
            "date": data.get("date"),
            "outcome": "rejected",
            "rejection_type": data.get("rejection_type", "gpt"),
            "gpt_bias": data.get("gpt_bias"),
            "gpt_confidence": data.get("gpt_confidence"),
            "gpt_analysis": data.get("gpt_analysis"),
            "ichimoku_reasons": data.get("ichimoku_reasons"),
            "news_check": data.get("news_check"),
            "signal_data": data.get("signal_data"),
        })

    trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return trades


@router.get("/trades/stats")
def get_trade_stats():
    db = get_firestore()
    trades_snap = db.collection_group("trades").stream()

    stats = {}
    for doc in trades_snap:
        t = doc.to_dict()
        if not t.get("entry") or not t.get("strategy"):
            continue
        strat = t["strategy"]
        if strat not in stats:
            stats[strat] = {"trades": [], "name": strat}
        stats[strat]["trades"].append(t)

    result = {}
    for strat, data in stats.items():
        trades = data["trades"]
        closed = [t for t in trades if t.get("outcome") not in (None, "open")]

        def _pnl_category(t):
            outcome = t.get("outcome", "")
            if outcome in ("win", "loss", "breakeven"):
                return outcome
            # auto_closed, max_hold_expired → classify by realized PnL
            pnl = t.get("realized_pnl", 0) or 0
            if pnl > 0:
                return "win"
            elif pnl < 0:
                return "loss"
            return "breakeven"

        wins = [t for t in closed if _pnl_category(t) == "win"]
        losses = [t for t in closed if _pnl_category(t) == "loss"]
        breakevens = [t for t in closed if _pnl_category(t) == "breakeven"]

        pnls = [t.get("realized_pnl", 0) for t in closed if t.get("realized_pnl") is not None]
        total_pnl = sum(pnls)
        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]

        result[strat] = {
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0,
            "avg_loss": round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0,
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
            "profit_factor": round(sum(win_pnls) / abs(sum(loss_pnls)), 2) if loss_pnls and sum(loss_pnls) != 0 else None,
            "pnl_history": sorted(
                [{"date": t.get("date"), "pnl": t.get("realized_pnl", 0)} for t in closed if t.get("realized_pnl") is not None],
                key=lambda x: x["date"] or ""
            ),
        }

    all_pnls = [v["total_pnl"] for v in result.values()]
    return {
        "strategies": result,
        "global_pnl": round(sum(all_pnls), 2),
    }


@router.delete("/trades")
def delete_trade(path: str = Query(...)):
    """Delete a trade document and its events subcollection."""
    try:
        db = get_firestore()
        trade_ref = db.document(path)

        # Delete events subcollection first
        for event_doc in trade_ref.collection("events").stream():
            event_doc.reference.delete()

        trade_ref.delete()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/trades/{oanda_trade_id}/events")
def get_trade_events(oanda_trade_id: str, path: str = Query(None)):
    try:
        db = get_firestore()

        trade_ref = None

        # Use direct Firestore path if provided (no index needed)
        if path:
            trade_ref = db.document(path)
        else:
            # Fallback: search by oanda_trade_id across all trades collections
            try:
                docs = db.collection_group("trades").where(
                    "oanda_trade_id", "==", oanda_trade_id
                ).stream()
                for doc in docs:
                    if doc.to_dict().get("entry"):
                        trade_ref = doc.reference
                        break
            except Exception:
                pass

        if not trade_ref:
            return []

        events = trade_ref.collection("events").order_by("timestamp").stream()
        return [e.to_dict() for e in events]
    except Exception:
        return []
