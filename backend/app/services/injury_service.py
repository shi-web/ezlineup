"""Service for fetching NBA injury reports via the nbainjuries package.

Uses https://github.com/mxufc29/nbainjuries which pulls official NBA
injury-report PDFs.  Falls back gracefully when the package or its
Java dependency is missing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

INJURY_PENALTY: dict[str, float] = {
    "Out": 1.0,
    "Doubtful": 0.75,
    "Questionable": 0.25,
    "Probable": 0.05,
    "Available": 0.0,
}


def normalize_name(name: str) -> str:
    """Normalize a player name for matching.

    Handles 'Last, First' (injury-report format) and
    'First Last' (roster-entry format).
    """
    name = name.strip()
    if "," in name:
        parts = name.split(",", 1)
        name = f"{parts[1].strip()} {parts[0].strip()}"
    return name.lower()


def fetch_injury_report() -> list[dict]:
    """Fetch the latest NBA injury report.

    Returns a list of dicts with keys:
        player_name  – normalized 'first last' (lowercase)
        display_name – original name from report
        status       – Out / Questionable / Doubtful / Probable / Available
        reason       – injury description
        team         – full team name
        game_date    – date string
    """
    try:
        from nbainjuries import injury  # requires Java + tabula-py

        now = datetime.now()
        raw = injury.get_reportdata(now)

        if not raw:
            for delta_hours in (1, 3, 6, 12, 24):
                earlier = now - timedelta(hours=delta_hours)
                raw = injury.get_reportdata(earlier)
                if raw:
                    break

        if not raw:
            return []

        results: list[dict] = []
        seen: set[str] = set()
        for record in raw:
            raw_name = record.get("Player Name", "")
            norm = normalize_name(raw_name)
            if norm in seen:
                continue
            seen.add(norm)
            results.append(
                {
                    "player_name": norm,
                    "display_name": raw_name,
                    "status": record.get("Current Status", "Unknown"),
                    "reason": record.get("Reason", ""),
                    "team": record.get("Team", ""),
                    "game_date": record.get("Game Date", ""),
                }
            )
        return results

    except ImportError:
        logger.info(
            "nbainjuries package not installed – injury data unavailable. "
            "Install with: pip install nbainjuries  (requires Java runtime)"
        )
        return []
    except Exception as e:
        logger.warning("Failed to fetch injury report: %s", e)
        return []


def build_injury_lookup(injuries: list[dict]) -> dict[str, dict]:
    """Build lookup dict: normalized player name → injury record."""
    return {r["player_name"]: r for r in injuries}


def get_injury_penalty(status: str) -> float:
    """Score reduction factor: 0.0 = no penalty, 1.0 = full exclusion."""
    return INJURY_PENALTY.get(status, 0.0)
