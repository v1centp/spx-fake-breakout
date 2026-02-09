from app.services.firebase import get_firestore
from datetime import datetime
import re
import requests
import os

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")  # à stocker dans Render en variable d'env

def log_to_slack(message: str, level: str = "INFO"):
    # 🔕 Ignorer tous les logs sauf ceux liés aux ordres de trading
    if level != "TRADING":
        return

    emoji = {
        "INFO": "ℹ️",
        "ERROR": "❌",
        "WARN": "⚠️",
        "TRADING": "📈",
        "RANGE": "📏",
        "OANDA": "💰",
    }.get(level, "🔍")

    payload = {
        "text": f"{emoji} *{level}* — {message}"
    }

    try:
        if SLACK_WEBHOOK_URL:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
    except Exception as e:
        print(f"⚠️ Erreur Slack : {e}")

_TAG_RE = re.compile(r"^\[([^\]]+)\]")


def _extract_tag(message: str) -> str | None:
    """Extract strategy/service tag from log message like '[trend_follow::I:NDX] ...'."""
    m = _TAG_RE.match(message)
    if not m:
        return None
    tag = m.group(1)
    # '[mean_revert::I:SPX]' -> 'mean_revert'
    if "::" in tag:
        tag = tag.split("::")[0]
    return tag


def log_to_firestore(message: str, level="INFO", extra_data=None):
    # Slack uniquement si c'est un ordre de trading
    # log_to_slack(message, level)

    try:
        db = get_firestore()
        log_entry = {
            "message": message,
            "level": level,
            "timestamp": datetime.utcnow().isoformat(),
        }
        tag = _extract_tag(message)
        if tag:
            log_entry["tag"] = tag
        if extra_data:
            log_entry.update(extra_data)
        db.collection("execution_logs").add(log_entry)
    except Exception as e:
        # Si Firestore échoue, on logue l'erreur sur Slack
        log_to_slack(f"Firestore logging failed: {e}", level="ERROR")


def log_trade_event(trade_ref, event_type: str, message: str, data: dict = None):
    """Log an event to a trade's events subcollection."""
    try:
        event = {
            "type": event_type,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }
        if data:
            event["data"] = data
        trade_ref.collection("events").add(event)
    except Exception as e:
        print(f"[log_trade_event] Failed: {e}")
