import firebase_admin
from firebase_admin import credentials, firestore
import os

# Charger la clé service (chemin depuis .env ou config)
cred_path = os.getenv("FIREBASE_CRED", "firebase-service-account.json")
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

# Client Firestore global
db = firestore.client()

def get_firestore():
    return db
