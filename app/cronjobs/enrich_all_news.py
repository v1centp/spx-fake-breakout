# server/app/cronjobs/enrich_all_news.py
import os, json
from datetime import datetime, timezone
from openai import OpenAI
from app.services.firebase import get_firestore

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
db = get_firestore()

SYSTEM_PROMPT = """
Tu es analyste financier. À partir d’un titre et potentiellement d’un résumé, tu extrais :
- tags : liste de mots‑clés
- type : "macro", "company", "breaking", "other"
- impact_score : float 0–1
- summary : résumé synthétique

Réponds **SEULEMENT** en JSON.
"""

def enrich():
    docs = db.collection("all_news").where("processed_by_gpt","==",False).limit(10).stream()
    for doc in docs:
        data = doc.to_dict()
        txt = data.get("title","") + (f"\n{data.get('description')}" if data.get("description") else "")
        resp = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role":"system","content":SYSTEM_PROMPT.strip()},
                {"role":"user","content":txt}
            ], temperature=0.2
        )
        raw = resp.choices[0].message.content.strip()
        try:
            js = json.loads(raw)
            doc.reference.update({
                "tags": js.get("tags",[]),
                "type": js.get("type"),
                "impact_score": js.get("impact_score"),
                "summary": js.get("summary"),
                "processed_by_gpt": True,
                "processed_at": datetime.now(timezone.utc).isoformat()
            })
            print("✅ Enriched:", data.get("title")[:50])
        except json.JSONDecodeError as e:
            print("❌ JSON decode error:", e, raw)

if __name__=="__main__":
    enrich()
