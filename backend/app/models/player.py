"""Player and stats models for internal use."""
from pydantic import BaseModel, Field


class PlayerStats(BaseModel):
    """Season stats for a player (from nba_api)."""

    player_id: int
    player_name: str
    team_abbreviation: str | None = None
    games_played: int = 0
    minutes_per_game: float = 0.0
    points_per_game: float = 0.0
    rebounds_per_game: float = 0.0
    assists_per_game: float = 0.0
    steals_per_game: float = 0.0
    blocks_per_game: float = 0.0
    fg3m_per_game: float = 0.0  # 3-pointers made per game (for fantasy scoring)
    tov_per_game: float = 0.0  # Turnovers per game
    fg_pct: float = 0.0
    fg3_pct: float = 0.0
    ft_pct: float = 0.0
    usage_pct: float | None = None  # If available from API
    season: str = ""
