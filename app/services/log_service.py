from app.services.firebase import get_firestore
from datetime import datetime
import requests
import os

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")  # à stocker dans Render en variable d'env
def log_to_slack(message: str, level: str = "INFO"):
    # Ne pas envoyer ce message spécifique sur Slack
    if level == "TRADING" and "Aucune condition de breakout valide détectée" in message:
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


def log_to_firestore(message: str, level="INFO", extra_data=None):
    log_to_slack(message, level)
    db = get_firestore()
    log_entry = {
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if extra_data:
        log_entry.update(extra_data)
    db.collection("execution_logs").add(log_entry)


