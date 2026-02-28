"""Fantasy scoring: uses FantasyScorer with league rules (multipliers and double-double bonuses)."""
from app.models.player import PlayerStats
from app.services.fantasy_scorer import DEFAULT_LEAGUE_RULES, FantasyScorer


def player_stats_to_dict(stats: PlayerStats) -> dict:
    """Convert PlayerStats to the dict format expected by FantasyScorer (per-game stats)."""
    return {
        "PTS": stats.points_per_game,
        "REB": stats.rebounds_per_game,
        "AST": stats.assists_per_game,
        "STL": stats.steals_per_game,
        "BLK": stats.blocks_per_game,
        "FG3M": stats.fg3m_per_game,
        "TOV": stats.tov_per_game,
    }


def project_fantasy_score(stats: PlayerStats, league_rules: dict | None = None) -> float:
    """Project fantasy score from season averages using league-rule multipliers.

    Double-double / triple-double bonuses are disabled here because those
    bonuses reward single-game achievements.  Checking whether *averages*
    cross the 10-stat threshold would incorrectly grant the bonus on every
    projected game for any player whose averages happen to be >= 10.
    """
    if stats.games_played == 0:
        return 0.0
    rules = dict(league_rules if league_rules is not None else DEFAULT_LEAGUE_RULES)
    rules["double_double_bonus"] = 0
    rules["triple_double_bonus"] = 0
    scorer = FantasyScorer(league_rules=rules)
    return scorer.calculate_score(player_stats_to_dict(stats))
