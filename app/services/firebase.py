import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

# Vérifie si c'est un JSON inline
json_str = os.getenv("FIREBASE_CRED")
if json_str and json_str.strip().startswith("{"):
    cred_path = "/tmp/firebase_key.json"
    with open(cred_path, "w") as f:
        f.write(json_str)
else:
    cred_path = os.getenv("FIREBASE_CRED", "firebase-service-account.json")

# Initialisation Firebase
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

# Client Firestore global
db = firestore.client()

def get_firestore():
    return db
