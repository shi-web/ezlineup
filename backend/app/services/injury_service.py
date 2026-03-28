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

# ── Trace log setup ───────────────────────────────────────────────────────────
_TRACE_LOG_PATH = os.path.join(os.path.dirname(__file__), "injury_trace.log")

def _trace(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    logger.debug(line)
    try:
        with open(_TRACE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning("Could not write injury trace log: %s", e)

# ── Constants ─────────────────────────────────────────────────────────────────

INJURY_PENALTY: dict[str, float] = {
    "Out": 1.0,
    "Doubtful": 0.75,
    "Questionable": 0.25,
    "GTD": 0.05,
    "Probable": 0.05,
    "Available": 0.0,
}

_MAX_SOURCE_AGE_HOURS = 36   # dated sources older than this are rejected
_TAVILY_DAYS          = 2    # Tavily API-level recency filter (days)

# ── Session cache ─────────────────────────────────────────────────────────────
# Lives only in memory (gone when the process restarts / session ends).
# Entries expire after _CACHE_TTL_HOURS so stale results don't persist
# across a long-running server process.

_CACHE_TTL_HOURS: float = 2.0   # how long a cached result stays valid

_injury_cache: dict[str, tuple[dict, datetime]] = {}
# key  → normalized player name
# value → (result_dict, fetched_at datetime in UTC)


def _cache_get(player_key: str) -> Optional[dict]:
    """Return cached result if it exists and is not expired, else None."""
    entry = _injury_cache.get(player_key)
    if entry is None:
        return None
    result, fetched_at = entry
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600.0
    if age_hours > _CACHE_TTL_HOURS:
        _trace(f"  CACHE EXPIRED for '{player_key}' (age={age_hours:.1f}h > TTL={_CACHE_TTL_HOURS}h) → will re-fetch")
        del _injury_cache[player_key]
        return None
    _trace(f"  CACHE HIT for '{player_key}' (age={age_hours:.1f}h) → skipping Tavily call")
    return result


def _cache_set(player_key: str, result: dict) -> None:
    """Store a result in the session cache."""
    _injury_cache[player_key] = (result, datetime.now(timezone.utc))
    _trace(f"  CACHE SET for '{player_key}' (TTL={_CACHE_TTL_HOURS}h)")



# ── Name normalization ────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    name = name.strip()
    if "," in name:
        parts = name.split(",", 1)
        name = f"{parts[1].strip()} {parts[0].strip()}"
    return name.lower()


# ── Datetime helpers ──────────────────────────────────────────────────────────

def _parse_datetime_safe(s: Optional[str]) -> Optional[str]:
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
    r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)", re.IGNORECASE
)
_URL_DATE_PAT = re.compile(r"(\d{4}-\d{2}-\d{2})")


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
    if m:
        hh   = int(m.group(2))
        mins = int(m.group(3)) if m.group(3) else 0
        ampm = m.group(4).upper()
        if ampm == "PM" and hh != 12:
            hh += 12
        if ampm == "AM" and hh == 12:
            hh = 0
        try:
            dt = datetime.fromisoformat(m.group(1)).replace(hour=hh, minute=mins, tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    m2 = _URL_DATE_PAT.search(url)
    if m2:
        try:
            dt = datetime.fromisoformat(m2.group(1)).replace(hour=12, tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return None


# ── Status / injury extraction ────────────────────────────────────────────────

_STATUS_PATTERNS = [
    ("Out",          r"\b(out|ruled out|will not play|inactive)\b"),
    ("Doubtful",     r"\b(doubtful)\b"),
    ("Questionable", r"\b(questionable)\b"),
    ("GTD",          r"\b(game[- ]time decision|gtd|probable)\b"),
    ("Available",    r"\b(active|available|will play|good to go)\b"),
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


# ── Source trust ranking ──────────────────────────────────────────────────────

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


# ── Tavily client (direct, supports `days` parameter) ────────────────────────

_tavily_client: Any = None


def _get_tavily_client() -> Any:
    global _tavily_client
    if _tavily_client is None:
        from tavily import TavilyClient  # type: ignore[import]
        _tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _tavily_client


def _tavily_search(query: str) -> dict:
    """Search restricted to the last _TAVILY_DAYS days at the API level."""
    client = _get_tavily_client()
    return client.search(
        query=query,
        max_results=5,
        search_depth="basic",
        include_answer=True,
        days=_TAVILY_DAYS,
    )


# ── Internal result processing ────────────────────────────────────────────────

def _process_player_result(player: str, tav: Any) -> dict:
    _trace(f"=== Processing player: {player} ===")

    answer_text = ""
    sources: list[dict] = []
    if isinstance(tav, dict):
        answer_text = tav.get("answer") or ""
        sources = tav.get("results") or []
    else:
        answer_text = str(tav)

    _trace(f"  Answer snippet: {answer_text[:120]!r}")
    _trace(f"  Total raw sources returned: {len(sources)}")

    if not sources and not answer_text:
        _trace("  DECISION: No data at all → Available")
        return {
            "player_name": normalize_name(player),
            "display_name": player,
            "status": "Available",
            "reason": None,
            "team": "",
            "game_date": "unknown",
        }

    # ── Build candidate list ──────────────────────────────────────────────
    candidates = []
    for i, r in enumerate(sources[:5]):
        title     = r.get("title", "")
        url       = r.get("url", "")
        published = r.get("published_date") or r.get("date") or None
        content   = r.get("content") or ""

        blob   = " ".join([title, content, answer_text]).strip()
        status = _extract_status(blob) or _extract_status(content) or _extract_status(title)
        injury_detail = _extract_injury_detail(blob)

        iso    = _parse_datetime_safe(published) or _extract_date_from_text(blob) or _extract_date_from_url(url)
        hours  = _hours_since(iso)
        trust  = _source_score(url)

        _trace(
            f"  Source[{i}]: trust={trust} | age={'%.1f' % hours if hours is not None else 'UNDATED'}h "
            f"| status={status} | url={url[:80]} | title={title[:60]!r}"
        )

        candidates.append({
            "status":        status,
            "injury_detail": injury_detail,
            "source":        url or title or None,
            "last_updated":  iso or "unknown",
            "hours_old":     hours,
            "trust_score":   trust,
        })

    # ── Freshness filter ──────────────────────────────────────────────────
    fresh = [c for c in candidates if c["hours_old"] is not None and c["hours_old"] <= _MAX_SOURCE_AGE_HOURS]

    for c in candidates:
        if c["hours_old"] is not None and c["hours_old"] > _MAX_SOURCE_AGE_HOURS:
            _trace(
                f"  REJECTED stale: age={c['hours_old']:.1f}h | "
                f"status={c['status']} | url={str(c['source'])[:80]}"
            )

    # Undated sources: only high-trust (>=3) survive, and "Out" is
    # downgraded to "Questionable" because we can't verify recency.
    undated_safe = []
    for c in candidates:
        if c["hours_old"] is not None:
            continue  # already handled above
        if c["trust_score"] < 3:
            _trace(
                f"  REJECTED undated low-trust: trust={c['trust_score']} | "
                f"status={c['status']} | url={str(c['source'])[:80]}"
            )
            continue
        if c["status"] == "Out":
            _trace(
                f"  UNDATED high-trust source says Out → downgraded to Questionable "
                f"(cannot confirm recency) | url={str(c['source'])[:80]}"
            )
            c = {**c, "status": "Questionable"}
        undated_safe.append(c)

    _trace(
        f"  After filters: {len(fresh)} fresh, {len(undated_safe)} undated-safe, "
        f"threshold={_MAX_SOURCE_AGE_HOURS}h"
    )

    active_candidates = fresh if fresh else undated_safe

    if not active_candidates:
        _trace(
            f"  DECISION: No usable sources after filtering → Available "
            f"(no confirmed recent injury)"
        )
        return {
            "player_name": normalize_name(player),
            "display_name": player,
            "status": "Available",
            "reason": f"No injury reports within last {_MAX_SOURCE_AGE_HOURS}h — assumed healthy.",
            "team": "",
            "game_date": "unknown",
        }

    statuses = [c["status"] for c in active_candidates if c["status"]]
    if not statuses:
        _trace("  DECISION: Usable sources found but no status parsed → Questionable")
        return {
            "player_name": normalize_name(player),
            "display_name": player,
            "status": "Questionable",
            "reason": "Status mentioned but could not be parsed.",
            "team": "",
            "game_date": "unknown",
        }

    # ── Majority vote ─────────────────────────────────────────────────────
    out_count       = statuses.count("Out")
    total           = len(statuses)
    unique_statuses = sorted(set(statuses))

    _trace(f"  Status votes: {statuses}")

    if out_count / total >= 0.5:
        final_status = "Out"
        _trace(f"  Vote: ≥50% Out ({out_count}/{total}) → Out")
    elif len(unique_statuses) > 1:
        final_status = max(set(statuses), key=statuses.count)
        _trace(f"  Vote: conflict {unique_statuses} → majority={final_status}")
    else:
        final_status = unique_statuses[0]
        _trace(f"  Vote: unanimous → {final_status}")

    conflict_note = (
        f"Conflicting reports ({', '.join(unique_statuses)}). "
        if len(unique_statuses) > 1 else ""
    )

    # ── Pick best source ──────────────────────────────────────────────────
    same_status = [c for c in active_candidates if c["status"] == final_status]
    pool = same_status or active_candidates
    chosen = sorted(
        pool,
        key=lambda c: (
            c["trust_score"],
            -(c["hours_old"] if c["hours_old"] is not None else 9999),
        ),
        reverse=True,
    )[0]

    _trace(
        f"  Chosen source: trust={chosen['trust_score']} | "
        f"age={'%.1f' % chosen['hours_old'] if chosen['hours_old'] is not None else 'UNDATED'}h | "
        f"url={str(chosen['source'])[:80]}"
    )
    _trace(f"  FINAL DECISION for {player}: {final_status}")
    _trace("")

    last_updated = chosen.get("last_updated", "unknown")
    hours        = chosen.get("hours_old")
    stale_note   = f"Source ~{int(hours)}h old. " if hours and hours > _MAX_SOURCE_AGE_HOURS else ""
    reason       = (stale_note + conflict_note + (chosen.get("injury_detail") or "")).strip() or None

    return {
        "player_name": normalize_name(player),
        "display_name": player,
        "status": final_status,
        "reason": reason,
        "team": "",
        "game_date": last_updated,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_injury_report(players: list[str] | None = None) -> list[dict]:
    """Fetch injury status for the given players via Tavily web search."""
    if not players:
        return []

    if not os.environ.get("TAVILY_API_KEY"):
        logger.warning("TAVILY_API_KEY not set – injury data unavailable.")
        return []

    _trace(f"fetch_injury_report called for {len(players)} player(s): {players}")
    _trace(f"Freshness threshold: {_MAX_SOURCE_AGE_HOURS}h | Tavily days filter: {_TAVILY_DAYS}")
    _trace("")

    try:
        _get_tavily_client()
    except ImportError:
        logger.info("tavily package not installed – run: pip install tavily-python")
        return []
    except Exception as e:
        logger.warning("Failed to initialize Tavily client: %s", e)
        return []

    results: list[dict] = []
    for player in players:
        player_key = normalize_name(player)

        # ── Check session cache first ─────────────────────────────────────
        cached = _cache_get(player_key)
        if cached is not None:
            results.append(cached)
            continue

        # ── Not cached (or expired) → fetch from Tavily ───────────────────
        query = f"{player} NBA injury status today questionable doubtful out game-time decision"
        _trace(f"Tavily query (days={_TAVILY_DAYS}): {query!r}")
        try:
            tav = _tavily_search(query)
        except Exception as e:
            logger.warning("Tavily search failed for %s: %s", player, e)
            _trace(f"  ERROR: Tavily search failed → {e}")
            continue

        result = _process_player_result(player, tav)
        _cache_set(player_key, result)
        results.append(result)

    return results


def build_injury_lookup(injuries: list[dict]) -> dict[str, dict]:
    return {r["player_name"]: r for r in injuries}


def get_injury_penalty(status: str) -> float:
    return INJURY_PENALTY.get(status, 0.0)