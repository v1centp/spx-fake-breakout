from fastapi import FastAPI
from app.routers import balance, positions, orders, strategy, sync
from fastapi.middleware.cors import CORSMiddleware 


app = FastAPI()

origins = [
   "http://localhost:3000",  # React dev server
    "http://localhost:5173",  # Vite dev server
    "https://algo-project-e5b83.web.app"  # Add this if deploying your frontend
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
app.include_router(sync.router)