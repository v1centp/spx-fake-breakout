from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware 

from app.routers import balance, positions, orders, strategy, market_data  # ⬅️ Add this

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
app.include_router(orders.router)
app.include_router(strategy.router)
app.include_router(market_data.router, prefix="/api")  # ⬅️ Add this line
