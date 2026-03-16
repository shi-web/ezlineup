"""Orchestrates lineup optimization: resolve IDs, fetch stats, check injuries, optimize."""
from app.models.lineup import (
    InjuryInfo,
    LineupRequest,
    LineupResponse,
    RosterEntry,
)
from app.services.injury_service import (
    build_injury_lookup,
    fetch_injury_report,
    normalize_name,
)
from app.services.nba_service import get_player_id, get_players_season_stats
from app.services.optimizer import optimize_lineup


def run_lineup_optimization(request: LineupRequest) -> LineupResponse:
    """Resolve roster → fetch stats → fetch injuries → optimize → return response."""

    # 1. Resolve player IDs
    resolved: list[RosterEntry] = []
    for entry in request.roster:
        pid = entry.player_id if entry.player_id is not None else get_player_id(entry.player_name)
        if pid is None:
            continue
        resolved.append(RosterEntry(player_name=entry.player_name, cost=entry.cost, player_id=pid))

    if not resolved:
        return LineupResponse(
            lineup=[],
            total_cost=0.0,
            total_projected_score=0.0,
            explanation="No players could be resolved. Check names and try again.",
        )

    # 2. Fetch season stats
    player_ids = [r.player_id for r in resolved if r.player_id is not None]
    stats_map = get_players_season_stats(player_ids)

    # 3. Fetch injury report (optional)
    injury_lookup: dict[str, dict] | None = None
    injury_infos: list[InjuryInfo] = []

    if request.consider_injuries:
        player_names = [e.player_name for e in resolved]
        raw_injuries = fetch_injury_report(player_names)
        if raw_injuries:
            injury_lookup = build_injury_lookup(raw_injuries)
            roster_names = {normalize_name(e.player_name) for e in resolved}
            for inj in raw_injuries:
                if inj["player_name"] in roster_names:
                    injury_infos.append(
                        InjuryInfo(
                            player_name=inj.get("display_name", inj["player_name"]),
                            status=inj["status"],
                            reason=inj.get("reason") or  "",
                            team=inj.get("team") or "",
                        )
                    )

    # 4. Optimize
    league_rules = request.scoring_rules.to_league_rules_dict() if request.scoring_rules else None
    lineup_results = optimize_lineup(
        resolved,
        stats_map,
        request.budget,
        league_rules=league_rules,
        injury_lookup=injury_lookup,
    )

    total_cost = sum(r.cost for r in lineup_results)
    total_score = sum(r.projected_score for r in lineup_results)

    # 5. Build explanation
    explanation_parts = [f"Selected {len(lineup_results)} players within ${request.budget:,.0f} budget."]
    if injury_infos:
        out_players = [i for i in injury_infos if i.status == "Out"]
        quest_players = [i for i in injury_infos if i.status == "Questionable"]
        if out_players:
            explanation_parts.append(
                f"{len(out_players)} roster player(s) ruled OUT and excluded: "
                + ", ".join(p.player_name for p in out_players)
                + "."
            )
        if quest_players:
            explanation_parts.append(
                f"{len(quest_players)} player(s) listed as Questionable (scores reduced 25%): "
                + ", ".join(p.player_name for p in quest_players)
                + "."
            )
    explanation_parts.append(
        f"Total projected fantasy score: {total_score:.1f}. "
        "Players ranked by value (projected score per salary dollar)."
    )

    return LineupResponse(
        lineup=lineup_results,
        total_cost=total_cost,
        total_projected_score=round(total_score, 2),
        explanation=" ".join(explanation_parts),
        injuries=injury_infos,
    )
