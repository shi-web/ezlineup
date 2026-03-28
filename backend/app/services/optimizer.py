"""Position-constrained lineup optimizer with injury awareness.

Selects exactly one player per filled position slot (PG, SG, SF, PF, C)
to maximize total projected fantasy score within the salary budget.

Algorithm:
  1. Score every candidate (apply injury penalties, exclude "Out" players).
  2. Group candidates by their assigned position slot.
  3. Enumerate all combinations across filled slots and pick the highest-scoring
     combination whose total cost fits within the budget.
  4. If no complete combination fits, fall back to a greedy per-slot selection
     using remaining budget.
"""
from __future__ import annotations

from itertools import product as iter_product

from app.models.lineup import LineupResult, RosterEntry
from app.models.player import PlayerStats
from app.services.injury_service import get_injury_penalty, normalize_name
from app.services.scoring import project_fantasy_score

VALID_POSITIONS = {"PG", "SG", "SF", "PF", "C"}


def optimize_lineup(
    roster: list[RosterEntry],
    stats_by_player_id: dict[int, PlayerStats],
    budget: float,
    league_rules: dict | None = None,
    injury_lookup: dict[str, dict] | None = None,
) -> list[LineupResult]:
    """Return the best one-player-per-position lineup within *budget*."""

    # ── 1. Score every candidate ──────────────────────────────────────────────
    candidates: list[dict] = []
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
            inj = injury_lookup.get(normalize_name(entry.player_name))
            if inj:
                injury_status = inj["status"]
                injury_note = inj.get("reason", "")
                penalty = get_injury_penalty(injury_status)
                if penalty >= 1.0:
                    continue  # player is Out — exclude entirely
                score = raw_score * (1 - penalty)

        candidates.append({
            "name": entry.player_name,
            "pid": pid,
            "cost": entry.cost,
            "score": score,
            "position": entry.position,
            "injury_status": injury_status,
            "injury_note": injury_note,
        })

    # ── 2. Group by position (ignore unknown positions) ───────────────────────
    groups: dict[str, list[dict]] = {}
    for c in candidates:
        if c["position"] in VALID_POSITIONS:
            groups.setdefault(c["position"], []).append(c)

    if not groups:
        return []

    # ── 3. Enumerate all combinations, pick best within budget ────────────────
    positions = list(groups.keys())
    best_score = -1.0
    best_combo: tuple[dict, ...] | None = None

    for combo in iter_product(*[groups[p] for p in positions]):
        if sum(c["cost"] for c in combo) <= budget:
            total = sum(c["score"] for c in combo)
            if total > best_score:
                best_score = total
                best_combo = combo

    if best_combo is not None:
        return [_to_result(c) for c in best_combo]

    # ── 4. Fallback: greedy per slot when no full combo fits budget ───────────
    # Fill cheapest positions first to preserve budget for remaining slots.
    remaining = budget
    result: list[LineupResult] = []
    for pos in sorted(positions, key=lambda p: min(c["cost"] for c in groups[p])):
        affordable = [c for c in groups[pos] if c["cost"] <= remaining]
        if affordable:
            best = max(affordable, key=lambda c: c["score"])
            result.append(_to_result(best))
            remaining -= best["cost"]
    return result


def _to_result(c: dict) -> LineupResult:
    return LineupResult(
        player_name=c["name"],
        player_id=c["pid"],
        cost=c["cost"],
        projected_score=round(c["score"], 2),
        injury_status=c["injury_status"],
        injury_note=c["injury_note"],
    )
