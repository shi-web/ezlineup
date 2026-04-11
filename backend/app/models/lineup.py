"""Request/response models for lineup optimization."""
from __future__ import annotations
from pydantic import BaseModel, Field


class RosterEntry(BaseModel):
    """A single player on the user's roster with optional cost."""

    player_name: str = Field(..., description="Player name (e.g. 'LeBron James')")
    cost: float = Field(default=0.0, ge=0, description="Fantasy cost/salary for this player")
    player_id: int | None = Field(default=None, description="Optional NBA player ID for exact match")
    position: str = Field(default="?", description="Fantasy position slot (PG, SG, SF, PF, C)")


class ScoringRules(BaseModel):
    """Optional custom scoring multipliers (overrides defaults if provided)."""

    pts_multiplier: float | None = None
    reb_multiplier: float | None = None
    ast_multiplier: float | None = None
    stl_multiplier: float | None = None
    blk_multiplier: float | None = None
    fg3m_multiplier: float | None = None  # 3pm multiplier
    tov_multiplier: float | None = None
    double_double_bonus: float | None = None
    triple_double_bonus: float | None = None

    def to_league_rules_dict(self) -> dict:
        """Convert to dict for FantasyScorer (only non-None keys)."""
        from app.services.fantasy_scorer import DEFAULT_LEAGUE_RULES
        out = dict(DEFAULT_LEAGUE_RULES)
        if self.pts_multiplier is not None:
            out["pts_multiplier"] = self.pts_multiplier
        if self.reb_multiplier is not None:
            out["reb_multiplier"] = self.reb_multiplier
        if self.ast_multiplier is not None:
            out["ast_multiplier"] = self.ast_multiplier
        if self.stl_multiplier is not None:
            out["stl_multiplier"] = self.stl_multiplier
        if self.blk_multiplier is not None:
            out["blk_multiplier"] = self.blk_multiplier
        if self.fg3m_multiplier is not None:
            out["3pm_multiplier"] = self.fg3m_multiplier
        if self.tov_multiplier is not None:
            out["tov_multiplier"] = self.tov_multiplier
        if self.double_double_bonus is not None:
            out["double_double_bonus"] = self.double_double_bonus
        if self.triple_double_bonus is not None:
            out["triple_double_bonus"] = self.triple_double_bonus
        return out


class LineupRequest(BaseModel):
    """Request body for optimal lineup."""

    roster: list[RosterEntry] = Field(..., description="User's roster with names and costs")
    budget: float = Field(..., ge=0, description="Total budget (sum of selected players' costs must be <= this)")
    league_rules: str | None = Field(default=None, description="Optional league rules text for RAG")
    scoring_rules: ScoringRules | None = Field(default=None, description="Optional custom scoring multipliers")
    consider_injuries: bool = Field(default=True, description="Fetch and factor in injury data")


class LineupResult(BaseModel):
    """One selected player in the recommended lineup."""

    player_name: str
    player_id: int | None
    cost: float
    projected_score: float
    reason: str = ""
    injury_status: str | None = None
    injury_note: str | None = None


class InjuryInfo(BaseModel):
    """Injury report entry for display."""

    player_name: str
    status: str
    reason: str = ""
    team: str = ""


class LineupResponse(BaseModel):
    """Response with optimal lineup and explanation."""

    lineup: list[LineupResult] = Field(..., description="Recommended players")
    total_cost: float = Field(..., description="Sum of selected players' costs")
    total_projected_score: float = Field(..., description="Sum of projected scores")
    explanation: str = Field(..., description="Natural language explanation from the agent")
    injuries: list[InjuryInfo] = Field(default_factory=list, description="Injury reports for roster players")
