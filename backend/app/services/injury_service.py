"""Service for fetching NBA injury reports via Tavily web search.

Uses the Tavily search API to look up current injury/status information
for specific players.  Falls back gracefully when the package or API key
is missing.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

INJURY_PENALTY: dict[str, float] = {
    "Out": 1.0,
    "Doubtful": 0.75,
    "Questionable": 0.25,
    "GTD": 0.05,
    "Probable": 0.05,
    "Available": 0.0,
}


# Name normalization

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


# Datetime helpers

def _parse_datetime_safe(s: Optional[str]) -> Optional[str]:
    """Return ISO string (UTC) if parseable, else None."""
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        dt = datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        pass
    return None


def _hours_since(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
    except Exception:
        return None


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_DATE_PAT = re.compile(
    r"\b(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
    r"|Sep(?:t)?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?,\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_URL_TS_PAT = re.compile(
    r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)", re.IGNORECASE
)


def _extract_date_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _DATE_PAT.search(text)
    if not m:
        return None
    mon_raw = m.group("mon").lower()
    mon = _MONTHS.get(mon_raw[:3], _MONTHS.get(mon_raw))
    if mon is None:
        return None
    try:
        dt = datetime(int(m.group("year")), mon, int(m.group("day")), 12, 0, 0, tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


def _extract_date_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _URL_TS_PAT.search(url)
    if not m:
        return None
    hh, mm = int(m.group(2)), int(m.group(3))
    ampm = m.group(4).upper()
    if ampm == "PM" and hh != 12:
        hh += 12
    if ampm == "AM" and hh == 12:
        hh = 0
    try:
        dt = datetime.fromisoformat(m.group(1)).replace(hour=hh, minute=mm, tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


# Status / injury extraction


_STATUS_PATTERNS = [
    ("Out", r"\b(out|ruled out|will not play|inactive)\b"),
    ("Doubtful", r"\b(doubtful)\b"),
    ("Questionable", r"\b(questionable)\b"),
    ("GTD", r"\b(game[- ]time decision|gtd|probable)\b"),
    ("Available", r"\b(active|available|will play|good to go)\b"),
]

_INJURY_PAT = re.compile(
    r"\b(ankle|knee|hamstring|groin|hip|back|wrist|hand|foot|toe|shoulder|elbow|illness|concussion|rest)\b",
    re.IGNORECASE,
)


def _extract_status(text: str) -> Optional[str]:
    t = (text or "").lower()
    for label, pat in _STATUS_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return label
    return None


def _extract_injury_detail(text: str) -> Optional[str]:
    if not text:
        return None
    m = _INJURY_PAT.search(text)
    if not m:
        return None
    idx = text.lower().find(m.group(0).lower())
    return text[max(0, idx - 50): min(len(text), idx + 80)].strip()


# Source trust ranking


def _source_score(src: Optional[str]) -> int:
    if not src:
        return 0
    s = src.lower()
    if "nba.com" in s or "ak-static.cms.nba.com" in s:
        return 5
    if "espn.com" in s:
        return 4
    if "rotowire" in s or "underdog" in s or "theathletic" in s:
        return 3
    if "yahoo.com" in s or "cbssports" in s:
        return 2
    if any(d in s for d in ("reddit.com", "facebook.com", "x.com", "twitter.com")):
        return 0
    return 1


# Brute Tavily search tool singleton


_search_tool: Any = None


def _get_search_tool() -> Any:
    global _search_tool
    if _search_tool is None:
        from langchain_tavily import TavilySearch  # type: ignore[import]
        _search_tool = TavilySearch(
            max_results=5,
            search_depth="basic",
            include_answer=True,
            include_raw=False,
        )
    return _search_tool


# Internal result processing


def _process_player_result(player: str, tav: Any) -> dict:
    """Translate a single Tavily search result into the standard injury dict."""
    answer_text = ""
    sources: list[dict] = []
    if isinstance(tav, dict):
        answer_text = tav.get("answer") or ""
        sources = tav.get("results") or []
    else:
        answer_text = str(tav)

    if not sources and not answer_text:
        return {
            "player_name": normalize_name(player),
            "display_name": player,
            "status": "Available",
            "reason": None,
            "team": "",
            "game_date": "unknown",
        }

    candidates = []
    for r in sources[:5]:
        title = r.get("title", "")
        url = r.get("url", "")
        published = r.get("published_date") or r.get("date") or None
        content = r.get("content") or ""

        blob = " ".join([title, content, answer_text]).strip()
        status = _extract_status(blob) or _extract_status(content) or _extract_status(title)
        injury_detail = _extract_injury_detail(blob)

        iso = _parse_datetime_safe(published) or _extract_date_from_text(blob) or _extract_date_from_url(url)
        candidates.append({
            "status": status,
            "injury_detail": injury_detail,
            "source": url or title or None,
            "last_updated": iso or "unknown",
        })

    statuses = [c["status"] for c in candidates if c["status"]]
    if not statuses:
        return {
            "player_name": normalize_name(player),
            "display_name": player,
            "status": "Questionable",  # conservative when status is unclear
            "reason": None,
            "team": "",
            "game_date": "unknown",
        }

    # Pick the most conservative status; flag conflicts in reason
    priority = {"Out": 4, "Doubtful": 3, "Questionable": 2, "GTD": 1, "Available": 0}

    #unique_statuses = sorted(set(statuses))
    #conflict_note = ""
    #if len(unique_statuses) > 1:
    #    conflict_note = f"Conflicting reports ({', '.join(unique_statuses)}). Using most conservative. "
    #final_status = sorted(unique_statuses, key=lambda s: priority.get(s, 2), reverse=True)[0]
    fresh_candidates = [
    c for c in candidates
    if c["status"] and (_hours_since(c["last_updated"] if c["last_updated"] != "unknown" else None) or 999) < 48]

    active_candidates = fresh_candidates if fresh_candidates else candidates
    statuses = [c["status"] for c in active_candidates if c["status"]]

    unique_statuses = sorted(set(statuses))
    conflict_note = ""

    # Only use most conservative if MAJORITY of sources agree it's bad
    out_count = statuses.count("Out")
    total = len(statuses)
    if out_count / total >= 0.5:
        final_status = "Out"
    elif len(unique_statuses) > 1:
        conflict_note = f"Conflicting reports ({', '.join(unique_statuses)}). "
    # Use majority vote instead of most conservative
        final_status = max(set(statuses), key=statuses.count)
    else:
        final_status = unique_statuses[0]

    # Select the highest-trust source that agrees with the final status
    same_status = [c for c in candidates if c["status"] == final_status]
    chosen = sorted(
        same_status or candidates,
        key=lambda c: (_source_score(c["source"]), c["last_updated"] != "unknown"),
        reverse=True,
    )[0]

    last_updated = chosen.get("last_updated", "unknown")
    hours = _hours_since(last_updated if last_updated != "unknown" else None)
    stale_note = f"Source is ~{int(hours)}h old (>48h). " if hours and hours > 48 else ""

    reason = (stale_note + conflict_note + (chosen.get("injury_detail") or "")).strip() or None

    return {
        "player_name": normalize_name(player),
        "display_name": player,
        "status": final_status,
        "reason": reason,
        "team": "",
        "game_date": last_updated,
    }


# Public API

def fetch_injury_report(players: list[str] | None = None) -> list[dict]:
    """Fetch injury status for the given players via Tavily web search.

    Returns a list of dicts with keys:
        player_name  – normalized 'first last' (lowercase)
        display_name – original player name as supplied
        status       – Out / Doubtful / Questionable / GTD / Available
        reason       – injury description or conflict note (may be None)
        team         – team name (empty; Tavily does not provide reliably)
        game_date    – ISO timestamp of the source article, or 'unknown'

    Returns an empty list if *players* is None/empty, the Tavily package is
    not installed, or TAVILY_API_KEY is not set.
    """
    if not players:
        return []

    if not os.environ.get("TAVILY_API_KEY"):
        logger.warning("TAVILY_API_KEY not set – injury data unavailable.")
        return []

    try:
        search_tool = _get_search_tool()
    except ImportError:
        logger.info(
            "langchain_tavily not installed - injury data unavailable. "
            "Install with: pip install langchain-tavily"
        )
        return []
    except Exception as e:
        logger.warning("Failed to initialize Tavily search tool: %s", e)
        return []

    results: list[dict] = []
    for player in players:
        query = (
            f"{player} injury status questionable doubtful out "
            "game-time decision NBA latest"
        )
        try:
            tav = search_tool.invoke({"query": query})
        except Exception as e:
            logger.warning("Tavily search failed for %s: %s", player, e)
            continue
        results.append(_process_player_result(player, tav))

    return results


def build_injury_lookup(injuries: list[dict]) -> dict[str, dict]:
    """Build lookup dict: normalized player name → injury record."""
    return {r["player_name"]: r for r in injuries}


def get_injury_penalty(status: str) -> float:
    """Score reduction factor: 0.0 = no penalty, 1.0 = full exclusion."""
    return INJURY_PENALTY.get(status, 0.0)