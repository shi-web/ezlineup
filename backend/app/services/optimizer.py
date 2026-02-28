"""Budget-constrained lineup optimizer with injury awareness."""
from app.models.lineup import LineupResult, RosterEntry
from app.models.player import PlayerStats
from app.services.injury_service import get_injury_penalty, normalize_name
from app.services.scoring import project_fantasy_score


def optimize_lineup(
    roster: list[RosterEntry],
    stats_by_player_id: dict[int, PlayerStats],
    budget: float,
    league_rules: dict | None = None,
    injury_lookup: dict[str, dict] | None = None,
) -> list[LineupResult]:
    """Select players to maximize projected score subject to total cost <= budget.

    Uses greedy selection by value/cost ratio.  Players flagged as "Out"
    are excluded; other injury statuses reduce the projected score.
    """
    candidates: list[tuple[str, int, float, float, str | None, str | None]] = []

    for entry in roster:
        pid = entry.player_id
        if pid is None:
            continue

        stats = stats_by_player_id.get(pid)
        raw_score = project_fantasy_score(stats, league_rules=league_rules) if stats else 0.0

        injury_status: str | None = None
        injury_note: str | None = None
        score = raw_score

        if injury_lookup:
            norm = normalize_name(entry.player_name)
            inj = injury_lookup.get(norm)
            if inj:
                injury_status = inj["status"]
                injury_note = inj.get("reason", "")
                penalty = get_injury_penalty(injury_status)
                if penalty >= 1.0:
                    continue  # fully exclude "Out" players
                score = raw_score * (1 - penalty)

        candidates.append((entry.player_name, pid, entry.cost, score, injury_status, injury_note))

    def value_key(item: tuple) -> float:
        _, _, cost, score, _, _ = item
        if cost <= 0:
            return float("inf") if score > 0 else 0
        return score / cost

    candidates.sort(key=value_key, reverse=True)

    selected: list[LineupResult] = []
    total_cost = 0.0
    for name, pid, cost, score, inj_status, inj_note in candidates:
        if total_cost + cost <= budget:
            selected.append(
                LineupResult(
                    player_name=name,
                    player_id=pid,
                    cost=cost,
                    projected_score=round(score, 2),
                    reason="",
                    injury_status=inj_status,
                    injury_note=inj_note,
                )
            )
            total_cost += cost

    return selected
