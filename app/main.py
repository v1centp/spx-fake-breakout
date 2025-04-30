from fastapi import FastAPI
from app.routers import balance, positions, orders, strategy, sync

app = FastAPI()

# Registering routers
app.include_router(balance.router)
app.include_router(positions.router)
app.include_router(orders.router)
# app.include_router(strategy.router)
app.include_router(sync.router)