# server/app/cronjobs/fetch_news_sources.py
import os, json
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
from app.services.firebase import get_firestore

POLY_KEY = os.getenv("POLYGON_API_KEY")
POLY_URL = "https://api.polygon.io/v2/reference/news"
CNBC_URL = "https://www.cnbc.com/markets/"
FF_CAL = "https://www.forexfactory.com/calendar.php"

db = get_firestore()

def fetch_polygon():
    since = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    params = {"order":"desc","limit":100,"sort":"published_utc","published_utc.gte":since,"apiKey":POLY_KEY}
    resp = requests.get(POLY_URL, params=params)
    for item in resp.json().get("results", []):
        doc = db.collection("all_news").document(item["id"])
        if doc.get().exists: continue
        doc.set({
            "source": "polygon", **{k: item.get(k) for k in ("title","description","tickers","published_utc","article_url")},
            "tags": [], "type": None, "impact_score": None,
            "processed_by_gpt": False, "fetched_at": datetime.now(timezone.utc).isoformat()
        })

def fetch_cnbc():
    r = requests.get(CNBC_URL)
    soup = BeautifulSoup(r.text, "html5lib")
    for a in soup.select("a.Card-title")[:20]:
        u = a["href"]; key="cnbc_"+str(hash(u))
        doc = db.collection("all_news").document(key)
        if doc.get().exists: continue
        doc.set({
            "source":"cnbc","title":a.text.strip(),"url":u,
            "description":None,"published_utc":None,
            "tags":[],"type":None,"impact_score":None,
            "processed_by_gpt":False,"fetched_at":datetime.now(timezone.utc).isoformat()
        })

def fetch_ff():
    r=requests.get(FF_CAL,params={"day":"today"})
    soup=BeautifulSoup(r.text,"html.parser")
    for row in soup.select("tr.calendar__row"):
        t=row.select_one(".calendar__time").text.strip()
        label=row.select_one(".calendar__event").text.strip()
        importance = ("high" if "high" in row.get("class",[]) else "medium" if "medium" in row.get("class",[]) else "low")
        key="ff_"+str(hash(label+t))
        doc=db.collection("all_news").document(key)
        if doc.get().exists: continue
        doc.set({
            "source":"forexfactory",
            "title":label,
            "description":None,
            "published_utc": None,
            "impact":importance,
            "tags":[],"type":None,"impact_score":None,
            "processed_by_gpt":False,"fetched_at":datetime.now(timezone.utc).isoformat()
        })

if __name__=="__main__":
    fetch_polygon(); fetch_cnbc(); fetch_ff()
