import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

# Vérifie si c'est un JSON inline (FIREBASE_CRED ou FIREBASE_CREDENTIALS_JSON)
json_str = os.getenv("FIREBASE_CRED", "")
if not json_str.strip().startswith("{"):
    json_str = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
if json_str and json_str.strip().startswith("{"):
    cred_path = "/tmp/firebase_key.json"
    # Parse and fix escaped newlines in private_key from .env
    parsed = json.loads(json_str)
    if "private_key" in parsed:
        parsed["private_key"] = parsed["private_key"].replace("\\n", "\n")
    with open(cred_path, "w") as f:
        json.dump(parsed, f)
else:
    cred_path = os.getenv("FIREBASE_CRED", "firebase-service-account.json")

# Initialisation Firebase
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

# Client Firestore global
db = firestore.client()

def get_firestore():
    return db
