"""League-rules-based fantasy basketball scoring (multipliers and double-double bonuses)."""


# Default multipliers matching common fantasy leagues; can be overridden per request
DEFAULT_LEAGUE_RULES = {
    "pts_multiplier": 1.0,
    "reb_multiplier": 1.2,
    "ast_multiplier": 1.5,
    "stl_multiplier": 3.0,
    "blk_multiplier": 3.0,
    "3pm_multiplier": 0.5,
    "tov_multiplier": -1.0,
    "double_double_bonus": 1.5,
    "triple_double_bonus": 3.0,
}


class FantasyScorer:
    """Calculate fantasy basketball scores from player stats using league rules."""

    def __init__(self, league_rules: dict | None = None):
        self.league_rules = league_rules if league_rules is not None else DEFAULT_LEAGUE_RULES.copy()

    def calculate_score(self, player_stats: dict) -> float:
        """
        Calculate fantasy score from player statistics.

        Args:
            player_stats: Dict with keys: PTS, REB, AST, STL, BLK, FG3M, TOV (per-game or single-game)

        Returns:
            float: Total fantasy points
        """
        score = 0.0

        # Core counting stats
        score += player_stats.get("PTS", 0) * self.league_rules.get("pts_multiplier", 1.0)
        score += player_stats.get("REB", 0) * self.league_rules.get("reb_multiplier", 1.2)
        score += player_stats.get("AST", 0) * self.league_rules.get("ast_multiplier", 1.5)
        score += player_stats.get("STL", 0) * self.league_rules.get("stl_multiplier", 3.0)
        score += player_stats.get("BLK", 0) * self.league_rules.get("blk_multiplier", 3.0)
        score += player_stats.get("FG3M", 0) * self.league_rules.get("3pm_multiplier", 0.5)
        score += player_stats.get("TOV", 0) * self.league_rules.get("tov_multiplier", -1.0)

        # Bonus for double-doubles (if league uses it)
        if self.league_rules.get("double_double_bonus", 0) > 0:
            dd_type = self._check_double_double(player_stats)
            if dd_type == "triple_double":
                score += self.league_rules.get("triple_double_bonus", 3.0)
            elif dd_type == "double_double":
                score += self.league_rules.get("double_double_bonus", 1.5)

        return round(score, 2)

    def _check_double_double(self, stats: dict) -> str | None:
        """Check for double-double or triple-double."""
        categories = [
            stats.get("PTS", 0),
            stats.get("REB", 0),
            stats.get("AST", 0),
            stats.get("STL", 0),
            stats.get("BLK", 0),
        ]
        double_digit = sum(1 for cat in categories if cat >= 10)
        if double_digit >= 3:
            return "triple_double"
        if double_digit >= 2:
            return "double_double"
        return None
