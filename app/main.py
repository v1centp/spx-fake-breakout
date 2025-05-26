from fastapi import FastAPI
from app.routers import balance, positions, orders, strategy, sync

app = FastAPI()

origins = [
   "http://localhost:3000",  # React dev server
    "http://localhost:5173",  # Vite dev server
    "https://algo-project-e5b83.web.app"  # Add this if deploying your frontend
]

# Registering routers
app.include_router(balance.router)
app.include_router(positions.router)
app.include_router(orders.router)
# app.include_router(strategy.router)
app.include_router(sync.router)