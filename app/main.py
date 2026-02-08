from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import balance, positions, strategy, market_data, logs, trades, webhook, news_test
from app.services.polygon_ws import start_polygon_ws
from app.services import trade_tracker
from app.services import news_scheduler
import threading


app = FastAPI()

origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "https://algo-project-e5b83.web.app",
    "http://localhost:8000",
    "http://localhost:5174"
]

app.add_middleware(
   CORSMiddleware,
   allow_origins=origins,
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)

app.include_router(balance.router)
app.include_router(positions.router)
app.include_router(strategy.router, prefix="/api")
app.include_router(market_data.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(webhook.router, prefix="/api")
app.include_router(news_test.router, prefix="/api")


@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=start_polygon_ws, daemon=True)
    thread.start()
    trade_tracker.start()
    news_scheduler.start()
