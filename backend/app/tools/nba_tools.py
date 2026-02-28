"""NBA stats and schedule tools (wrap nba_service for LangChain)."""
from langchain_core.tools import tool

from app.services.nba_service import get_player_id, get_player_season_stats, get_players_season_stats


@tool
def nba_player_stats_tool(player_name: str, season: str | None = None) -> str:
    """Fetch current NBA season statistics for a player by name.
    Use this when you need points, rebounds, assists, minutes, or other stats for a player.
    player_name: Full name, e.g. 'LeBron James'. season: Optional, e.g. '2024-25'. Defaults to current season.
    """
    pid = get_player_id(player_name)
    if pid is None:
        return f"Player not found: {player_name}"
    stats = get_player_season_stats(pid, season)
    if stats is None:
        return f"Could not fetch stats for {player_name}"
    return (
        f"{stats.player_name} ({stats.season}): "
        f"GP={stats.games_played}, MIN={stats.minutes_per_game:.1f}, PTS={stats.points_per_game:.1f}, "
        f"REB={stats.rebounds_per_game:.1f}, AST={stats.assists_per_game:.1f}, "
        f"STL={stats.steals_per_game:.1f}, BLK={stats.blocks_per_game:.1f}, "
        f"FG%={stats.fg_pct:.2f}, 3P%={stats.fg3_pct:.2f}, FT%={stats.ft_pct:.2f}"
    )


@tool
def nba_schedule_tool(player_name: str | None = None, season: str | None = None) -> str:
    """Get current NBA season context (season string and optional player game count).
    Use to know which season we are in and how many games a player has played.
    player_name: Optional; if provided, returns that player's games played this season.
    """
    from app.services.nba_service import _current_season
    s = season or _current_season()
    if not player_name:
        return f"Current NBA season: {s}. Use nba_player_stats_tool for a specific player's stats and games played."
    pid = get_player_id(player_name)
    if pid is None:
        return f"Player not found: {player_name}"
    stats = get_player_season_stats(pid, s)
    if stats is None:
        return f"Could not fetch data for {player_name}"
    return f"{stats.player_name} in {s}: {stats.games_played} games played."


def get_nba_stats_for_roster(player_names: list[str], season: str | None = None):
    """Resolve names to IDs and fetch stats for all. Used by optimizer/agent (not a LangChain tool)."""
    from app.models.player import PlayerStats
    ids = []
    name_to_id = {}
    for name in player_names:
        pid = get_player_id(name)
        if pid is not None:
            ids.append(pid)
            name_to_id[name] = pid
    stats_map = get_players_season_stats(ids, season)
    return stats_map
