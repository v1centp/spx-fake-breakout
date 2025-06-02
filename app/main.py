from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware 
from app.routers import balance, positions, strategy, market_data, logs  # ⬅️ Add this
from app.services.polygon_ws import start_polygon_ws
import threading


start_polygon_ws()


app = FastAPI()

origins = [
    "http://localhost:3000",  # React dev server
    "http://localhost:5173",  # Vite dev server
    "https://algo-project-e5b83.web.app",  # Deployed frontend
    "http://localhost:8000",  # FastAPI dev server
]

app.add_middleware(
   CORSMiddleware,
   allow_origins=origins,
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)

# Registering routers
app.include_router(balance.router)
app.include_router(positions.router)
app.include_router(strategy.router, prefix="/api")
app.include_router(market_data.router, prefix="/api")  # ⬅️ Add this line
app.include_router(logs.router, prefix="/api")  # ⬅️ Add this line

# Démarrage du WS dans un thread pour ne pas bloquer FastAPI
@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=start_polygon_ws, daemon=True)
    thread.start()
