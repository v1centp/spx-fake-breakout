import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

# 🔐 Charger la clé depuis une variable d’environnement (Render)
cred_json = os.getenv("FIREBASE_CREDENTIALS_JSON")
if not cred_json:
    raise RuntimeError("FIREBASE_CREDENTIALS_JSON not set in environment variables")

# ✅ Charger les credentials depuis le JSON
cred_dict = json.loads(cred_json)
cred = credentials.Certificate(cred_dict)

# 🔄 Initialiser Firebase
firebase_admin.initialize_app(cred)

# 📦 Client Firestore global
db = firestore.client()

def get_firestore():
    return db
