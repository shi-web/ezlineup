"""Lineup optimization and injury report API."""
from fastapi import APIRouter

from app.models.lineup import InjuryInfo, LineupRequest, LineupResponse
from app.services.injury_service import fetch_injury_report
from app.services.lineup_service import run_lineup_optimization

router = APIRouter()


@router.post("/lineup", response_model=LineupResponse)
async def get_optimal_lineup(request: LineupRequest):
    """Compute optimal lineup given roster, budget, and optional league rules."""
    return run_lineup_optimization(request)


@router.get("/injuries", response_model=list[InjuryInfo])
async def get_injuries():
    """Return the current NBA injury report."""
    raw = fetch_injury_report()
    return [
        InjuryInfo(
            player_name=r.get("display_name", r["player_name"]),
            status=r["status"],
            reason=r.get("reason", ""),
            team=r.get("team", ""),
        )
        for r in raw
    ]
