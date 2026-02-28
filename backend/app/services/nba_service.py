"""Wrapper around nba_api for player stats.

Data source priority:
  1. nba_api LeagueLeaders – single bulk call, most reliable endpoint
  2. nba_api PlayerGameLog – per-player fallback
  3. Built-in sample data  – ~20 popular players, always works
"""
from __future__ import annotations

import logging
import time
import unicodedata
from datetime import datetime

from nba_api.stats.static import players as static_players

from app.models.player import PlayerStats

logger = logging.getLogger(__name__)

CUSTOM_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
}

API_TIMEOUT = 20

_bulk_cache: dict[int, PlayerStats] | None = None
_bulk_tried: bool = False
_nba_api_dead: bool = False

# ---------------------------------------------------------------------------
# Sample data – approximate 2024-25 season averages for popular players.
# ---------------------------------------------------------------------------
_SAMPLE_STATS: dict[str, dict] = {
    "lebron james":        {"pts": 23.5, "reb": 7.5, "ast": 9.0, "stl": 1.2, "blk": 0.6, "fg3m": 1.8, "tov": 3.5, "gp": 60, "team": "LAL"},
    "stephen curry":       {"pts": 26.0, "reb": 4.5, "ast": 6.2, "stl": 0.9, "blk": 0.3, "fg3m": 4.5, "tov": 2.8, "gp": 62, "team": "GSW"},
    "kevin durant":        {"pts": 27.2, "reb": 6.8, "ast": 4.5, "stl": 0.7, "blk": 1.2, "fg3m": 2.0, "tov": 2.5, "gp": 55, "team": "PHX"},
    "giannis antetokounmpo": {"pts": 30.5, "reb": 11.5, "ast": 5.8, "stl": 1.1, "blk": 1.5, "fg3m": 0.7, "tov": 3.4, "gp": 58, "team": "MIL"},
    "nikola jokic":        {"pts": 26.5, "reb": 12.2, "ast": 9.0, "stl": 1.4, "blk": 0.8, "fg3m": 1.2, "tov": 3.0, "gp": 65, "team": "DEN"},
    "jayson tatum":        {"pts": 27.0, "reb": 8.5, "ast": 4.8, "stl": 1.0, "blk": 0.7, "fg3m": 2.8, "tov": 2.5, "gp": 60, "team": "BOS"},
    "luka doncic":         {"pts": 28.5, "reb": 8.0, "ast": 8.5, "stl": 1.3, "blk": 0.4, "fg3m": 3.0, "tov": 3.5, "gp": 50, "team": "DAL"},
    "joel embiid":         {"pts": 28.0, "reb": 10.0, "ast": 3.5, "stl": 0.8, "blk": 1.6, "fg3m": 1.5, "tov": 3.0, "gp": 40, "team": "PHI"},
    "shai gilgeous-alexander": {"pts": 31.0, "reb": 5.5, "ast": 6.0, "stl": 2.0, "blk": 1.0, "fg3m": 1.5, "tov": 2.2, "gp": 62, "team": "OKC"},
    "anthony edwards":     {"pts": 26.0, "reb": 5.5, "ast": 5.2, "stl": 1.5, "blk": 0.5, "fg3m": 2.5, "tov": 2.8, "gp": 63, "team": "MIN"},
    "devin booker":        {"pts": 25.5, "reb": 4.2, "ast": 6.5, "stl": 0.9, "blk": 0.3, "fg3m": 2.2, "tov": 2.5, "gp": 58, "team": "PHX"},
    "jimmy butler":        {"pts": 20.0, "reb": 5.8, "ast": 5.0, "stl": 1.5, "blk": 0.4, "fg3m": 0.5, "tov": 2.0, "gp": 45, "team": "MIA"},
    "damian lillard":      {"pts": 25.0, "reb": 4.0, "ast": 7.0, "stl": 0.9, "blk": 0.3, "fg3m": 3.2, "tov": 2.8, "gp": 60, "team": "MIL"},
    "anthony davis":       {"pts": 24.5, "reb": 11.5, "ast": 3.5, "stl": 1.2, "blk": 2.0, "fg3m": 0.5, "tov": 2.0, "gp": 55, "team": "LAL"},
    "donovan mitchell":    {"pts": 24.5, "reb": 4.5, "ast": 4.8, "stl": 1.6, "blk": 0.4, "fg3m": 2.8, "tov": 2.5, "gp": 58, "team": "CLE"},
    "trae young":          {"pts": 25.5, "reb": 3.0, "ast": 10.5, "stl": 1.0, "blk": 0.2, "fg3m": 2.5, "tov": 4.0, "gp": 60, "team": "ATL"},
    "ja morant":           {"pts": 22.0, "reb": 5.0, "ast": 8.0, "stl": 1.0, "blk": 0.4, "fg3m": 1.0, "tov": 3.0, "gp": 50, "team": "MEM"},
    "paul george":         {"pts": 22.0, "reb": 5.5, "ast": 4.0, "stl": 1.4, "blk": 0.4, "fg3m": 2.5, "tov": 2.5, "gp": 55, "team": "PHI"},
    "kawhi leonard":       {"pts": 23.0, "reb": 6.0, "ast": 4.0, "stl": 1.5, "blk": 0.5, "fg3m": 1.8, "tov": 2.0, "gp": 45, "team": "LAC"},
    "tyrese haliburton":   {"pts": 18.5, "reb": 3.5, "ast": 9.5, "stl": 1.2, "blk": 0.3, "fg3m": 2.5, "tov": 2.2, "gp": 55, "team": "IND"},
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _current_season() -> str:
    now = datetime.now()
    if now.month >= 10:
        return f"{now.year}-{str(now.year + 1)[2:]}"
    return f"{now.year - 1}-{str(now.year)[2:]}"


def get_player_id(name: str) -> int | None:
    """Resolve player name → NBA player ID (static list, no network)."""
    matches = static_players.find_players_by_full_name(name)
    if not matches:
        return None
    active = [m for m in matches if m.get("is_active")]
    return (active or matches)[0]["id"]


def _resolve_name(player_id: int) -> str:
    for p in static_players.get_players():
        if p["id"] == player_id:
            return p["full_name"]
    return str(player_id)


def _safe_mean(df, col: str) -> float:
    if col not in df.columns:
        return 0.0
    return float(df[col].mean())


def _ascii(s: str) -> str:
    """Strip diacritics: Jokić → Jokic, Dončić → Doncic."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


# ── source 1: nba_api LeagueLeaders (single call, most reliable endpoint) ────

def _fetch_all_player_stats_bulk(season: str) -> dict[int, PlayerStats] | None:
    """Fetch per-game stats for all NBA players via LeagueLeaders.

    This endpoint is significantly more reliable than LeagueDashPlayerStats
    or PlayerGameLog on stats.nba.com.
    """
    global _bulk_cache, _bulk_tried, _nba_api_dead
    if _bulk_tried:
        return _bulk_cache
    _bulk_tried = True

    if _nba_api_dead:
        return None

    try:
        from nba_api.stats.endpoints import leagueleaders

        leaders = leagueleaders.LeagueLeaders(
            season=season,
            stat_category_abbreviation="PTS",
            per_mode48="PerGame",
            headers=CUSTOM_HEADERS,
            timeout=API_TIMEOUT,
        )
        frames = leaders.get_data_frames()
        if not frames or frames[0] is None or frames[0].empty:
            logger.warning("LeagueLeaders returned no data")
            return None

        df = frames[0]
        cache: dict[int, PlayerStats] = {}
        for _, row in df.iterrows():
            pid = int(row["PLAYER_ID"])
            cache[pid] = PlayerStats(
                player_id=pid,
                player_name=str(row.get("PLAYER", _resolve_name(pid))),
                team_abbreviation=str(row.get("TEAM", "")),
                games_played=int(row.get("GP", 0)),
                minutes_per_game=float(row.get("MIN", 0)),
                points_per_game=float(row.get("PTS", 0)),
                rebounds_per_game=float(row.get("REB", 0)),
                assists_per_game=float(row.get("AST", 0)),
                steals_per_game=float(row.get("STL", 0)),
                blocks_per_game=float(row.get("BLK", 0)),
                fg3m_per_game=float(row.get("FG3M", 0)),
                tov_per_game=float(row.get("TOV", 0)),
                fg_pct=float(row.get("FG_PCT", 0)),
                fg3_pct=float(row.get("FG3_PCT", 0)),
                ft_pct=float(row.get("FT_PCT", 0)),
                season=season,
            )
        _bulk_cache = cache
        logger.info("LeagueLeaders: fetched live stats for %d players", len(cache))
        return cache

    except Exception as e:
        logger.warning("LeagueLeaders failed: %s", e)
        _nba_api_dead = True
        return None


# ── source 2: nba_api per-player (PlayerGameLog) ────────────────────────────

def _try_single_player(player_id: int, season: str) -> PlayerStats | None:
    global _nba_api_dead
    if _nba_api_dead:
        return None
    try:
        from nba_api.stats.endpoints import playergamelog

        log = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star="Regular Season",
            headers=CUSTOM_HEADERS,
            timeout=API_TIMEOUT,
        )
        frames = log.get_data_frames()
        if not frames or frames[0] is None or frames[0].empty:
            return PlayerStats(
                player_id=player_id,
                player_name=_resolve_name(player_id),
                games_played=0,
                season=season,
            )
        df = frames[0]
        matchup = df.iloc[0].get("MATCHUP", "") if "MATCHUP" in df.columns else ""
        return PlayerStats(
            player_id=player_id,
            player_name=_resolve_name(player_id),
            team_abbreviation=matchup.split()[0] if matchup else None,
            games_played=int(len(df)),
            minutes_per_game=_safe_mean(df, "MIN"),
            points_per_game=_safe_mean(df, "PTS"),
            rebounds_per_game=_safe_mean(df, "REB"),
            assists_per_game=_safe_mean(df, "AST"),
            steals_per_game=_safe_mean(df, "STL"),
            blocks_per_game=_safe_mean(df, "BLK"),
            fg3m_per_game=_safe_mean(df, "FG3M"),
            tov_per_game=_safe_mean(df, "TOV"),
            fg_pct=_safe_mean(df, "FG_PCT"),
            fg3_pct=_safe_mean(df, "FG3_PCT"),
            ft_pct=_safe_mean(df, "FT_PCT"),
            season=season,
        )
    except Exception as e:
        logger.warning("nba_api single-player %s: %s", player_id, e)
        _nba_api_dead = True
        return None


# ── source 3: sample data ───────────────────────────────────────────────────

def _from_sample(player_id: int, player_name: str, season: str) -> PlayerStats | None:
    sample = _SAMPLE_STATS.get(player_name.lower()) or _SAMPLE_STATS.get(_ascii(player_name).lower())
    if not sample:
        return None
    logger.info("Using sample data for %s", player_name)
    return PlayerStats(
        player_id=player_id,
        player_name=player_name,
        team_abbreviation=sample["team"],
        games_played=sample["gp"],
        points_per_game=sample["pts"],
        rebounds_per_game=sample["reb"],
        assists_per_game=sample["ast"],
        steals_per_game=sample["stl"],
        blocks_per_game=sample["blk"],
        fg3m_per_game=sample["fg3m"],
        tov_per_game=sample["tov"],
        season=season,
    )


# ── public API ───────────────────────────────────────────────────────────────

def get_player_season_stats(player_id: int, season: str | None = None) -> PlayerStats | None:
    """Get stats for one player through the cascade of sources."""
    season = season or _current_season()
    player_name = _resolve_name(player_id)

    if _bulk_cache and player_id in _bulk_cache:
        return _bulk_cache[player_id]

    result = _try_single_player(player_id, season)
    if result:
        return result

    return _from_sample(player_id, player_name, season)


def get_players_season_stats(
    player_ids: list[int], season: str | None = None
) -> dict[int, PlayerStats]:
    """Fetch season stats for multiple players.

    Cascade: LeagueLeaders bulk → per-player → sample data.
    """
    season = season or _current_season()
    result: dict[int, PlayerStats] = {}

    # 1) LeagueLeaders bulk (single call for ALL players)
    bulk = _fetch_all_player_stats_bulk(season)
    if bulk:
        for pid in player_ids:
            if pid in bulk:
                result[pid] = bulk[pid]
        missing = [pid for pid in player_ids if pid not in result]
        if not missing:
            return result
        player_ids = missing

    # 2) Per-player + 3) sample data
    for i, pid in enumerate(player_ids):
        if i > 0 and not _nba_api_dead:
            time.sleep(0.8)
        stats = _try_single_player(pid, season)
        if stats:
            result[pid] = stats
        else:
            sample = _from_sample(pid, _resolve_name(pid), season)
            if sample:
                result[pid] = sample

    return result
