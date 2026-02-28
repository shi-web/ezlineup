"""Health check endpoint."""
from fastapi import APIRouter

router = APIRouter()


@router.get("")
def health():
    """Return service health status."""
    return {"status": "ok", "service": "EZLineup"}
