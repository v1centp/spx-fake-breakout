from fastapi import APIRouter

router = APIRouter()

@router.get("/positions")
def get_positions():
    return {"status": "ok"}
