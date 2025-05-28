from app.services.firebase import get_firestore
from datetime import datetime

def log_to_firestore(message: str, level="INFO", extra_data=None):
    db = get_firestore()
    log_entry = {
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if extra_data:
        log_entry.update(extra_data)
    db.collection("execution_logs").add(log_entry)
