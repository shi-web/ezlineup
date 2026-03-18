"""Wrapper around nba_api for player stats.

Data source priority:
  1. nba_api LeagueLeaders – single bulk call, most reliable endpoint
  2. nba_api PlayerGameLog – per-player fallback
  3. Built-in sample data  – ~150 players across all 30 teams, always works
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

API_TIMEOUT = 60
MAX_RETRIES = 3
RETRY_DELAY = 3

_bulk_cache: dict[int, PlayerStats] | None = None
_bulk_tried: bool = False
_bulk_cache_ts: float = 0.0
_nba_api_dead: bool = False
CACHE_TTL_SECONDS = 60 * 60 * 6  # 6 hours

# ---------------------------------------------------------------------------
# Sample data – approximate 2025-26 season averages, all 30 teams.
# ---------------------------------------------------------------------------
_SAMPLE_STATS: dict[str, dict] = {
    # ── Atlanta Hawks ────────────────────────────────────────────────────────
    "trae young":               {"pts": 23.5, "reb": 3.2, "ast": 10.8, "stl": 1.1, "blk": 0.2, "fg3m": 2.4, "tov": 4.0, "gp": 55, "team": "ATL"},
    "dejounte murray":          {"pts": 21.0, "reb": 5.5, "ast": 5.8, "stl": 1.8, "blk": 0.4, "fg3m": 1.8, "tov": 2.5, "gp": 52, "team": "ATL"},
    "jalen johnson":            {"pts": 18.5, "reb": 8.0, "ast": 4.5, "stl": 1.2, "blk": 0.8, "fg3m": 1.2, "tov": 2.0, "gp": 58, "team": "ATL"},
    "clint capela":             {"pts": 11.0, "reb": 10.5, "ast": 1.0, "stl": 0.6, "blk": 1.8, "fg3m": 0.0, "tov": 1.5, "gp": 50, "team": "ATL"},
    "bogdan bogdanovic":        {"pts": 14.0, "reb": 3.0, "ast": 2.5, "stl": 0.8, "blk": 0.2, "fg3m": 2.5, "tov": 1.5, "gp": 48, "team": "ATL"},

    # ── Boston Celtics ───────────────────────────────────────────────────────
    "jayson tatum":             {"pts": 27.0, "reb": 8.5, "ast": 4.8, "stl": 1.0, "blk": 0.7, "fg3m": 2.8, "tov": 2.5, "gp": 60, "team": "BOS"},
    "jaylen brown":             {"pts": 23.5, "reb": 5.5, "ast": 3.5, "stl": 1.1, "blk": 0.4, "fg3m": 2.2, "tov": 2.2, "gp": 58, "team": "BOS"},
    "jrue holiday":             {"pts": 12.5, "reb": 4.5, "ast": 5.0, "stl": 1.5, "blk": 0.5, "fg3m": 1.5, "tov": 1.8, "gp": 62, "team": "BOS"},
    "al horford":               {"pts": 9.5,  "reb": 6.5, "ast": 2.5, "stl": 0.7, "blk": 1.0, "fg3m": 1.5, "tov": 1.2, "gp": 55, "team": "BOS"},
    "kristaps porzingis":       {"pts": 18.5, "reb": 7.0, "ast": 2.0, "stl": 0.8, "blk": 1.8, "fg3m": 2.0, "tov": 1.8, "gp": 42, "team": "BOS"},
    "payton pritchard":         {"pts": 15.0, "reb": 3.0, "ast": 3.5, "stl": 0.8, "blk": 0.2, "fg3m": 3.0, "tov": 1.5, "gp": 65, "team": "BOS"},

    # ── Brooklyn Nets ────────────────────────────────────────────────────────
    "cam thomas":               {"pts": 22.0, "reb": 3.5, "ast": 3.0, "stl": 0.9, "blk": 0.4, "fg3m": 1.8, "tov": 2.0, "gp": 58, "team": "BKN"},
    "ben simmons":              {"pts": 8.0,  "reb": 6.5, "ast": 5.5, "stl": 1.2, "blk": 0.8, "fg3m": 0.0, "tov": 2.0, "gp": 40, "team": "BKN"},
    "nic claxton":              {"pts": 12.0, "reb": 9.0, "ast": 2.0, "stl": 0.8, "blk": 2.2, "fg3m": 0.0, "tov": 1.8, "gp": 55, "team": "BKN"},
    "dennis schroder":          {"pts": 14.0, "reb": 3.0, "ast": 5.5, "stl": 1.0, "blk": 0.2, "fg3m": 1.5, "tov": 2.0, "gp": 60, "team": "BKN"},

    # ── Charlotte Hornets ────────────────────────────────────────────────────
    "lamelo ball":              {"pts": 24.5, "reb": 5.5, "ast": 8.5, "stl": 1.5, "blk": 0.4, "fg3m": 3.0, "tov": 3.5, "gp": 55, "team": "CHA"},
    "brandon miller":           {"pts": 19.0, "reb": 4.5, "ast": 2.5, "stl": 1.0, "blk": 0.5, "fg3m": 2.5, "tov": 1.8, "gp": 60, "team": "CHA"},
    "miles bridges":            {"pts": 18.0, "reb": 6.0, "ast": 3.0, "stl": 1.0, "blk": 0.5, "fg3m": 1.8, "tov": 1.8, "gp": 58, "team": "CHA"},
    "mark williams":            {"pts": 13.0, "reb": 9.5, "ast": 1.5, "stl": 0.5, "blk": 1.8, "fg3m": 0.0, "tov": 1.5, "gp": 52, "team": "CHA"},

    # ── Chicago Bulls ────────────────────────────────────────────────────────
    "zach lavine":              {"pts": 22.5, "reb": 4.5, "ast": 4.5, "stl": 0.9, "blk": 0.4, "fg3m": 2.5, "tov": 2.5, "gp": 50, "team": "CHI"},
    "nikola vucevic":           {"pts": 17.0, "reb": 10.5, "ast": 3.0, "stl": 0.7, "blk": 0.8, "fg3m": 1.5, "tov": 2.0, "gp": 60, "team": "CHI"},
    "coby white":               {"pts": 19.5, "reb": 4.0, "ast": 5.0, "stl": 1.0, "blk": 0.3, "fg3m": 2.8, "tov": 2.0, "gp": 62, "team": "CHI"},
    "ayo dosunmu":              {"pts": 12.0, "reb": 3.5, "ast": 4.0, "stl": 1.2, "blk": 0.3, "fg3m": 1.2, "tov": 1.5, "gp": 60, "team": "CHI"},

    # ── Cleveland Cavaliers ──────────────────────────────────────────────────
    "donovan mitchell":         {"pts": 24.5, "reb": 4.5, "ast": 4.8, "stl": 1.6, "blk": 0.4, "fg3m": 2.8, "tov": 2.5, "gp": 58, "team": "CLE"},
    "darius garland":           {"pts": 20.0, "reb": 3.0, "ast": 6.5, "stl": 1.2, "blk": 0.2, "fg3m": 2.2, "tov": 2.5, "gp": 55, "team": "CLE"},
    "evan mobley":              {"pts": 18.0, "reb": 9.0, "ast": 3.0, "stl": 1.0, "blk": 2.0, "fg3m": 1.0, "tov": 1.8, "gp": 62, "team": "CLE"},
    "jarrett allen":            {"pts": 13.5, "reb": 10.0, "ast": 1.5, "stl": 0.7, "blk": 1.2, "fg3m": 0.0, "tov": 1.5, "gp": 58, "team": "CLE"},
    "max strus":                {"pts": 11.0, "reb": 3.5, "ast": 2.0, "stl": 0.8, "blk": 0.3, "fg3m": 2.5, "tov": 1.2, "gp": 55, "team": "CLE"},

    # ── Dallas Mavericks ─────────────────────────────────────────────────────
    "luka doncic":              {"pts": 32.9, "reb": 8.5, "ast": 8.0, "stl": 1.3, "blk": 0.4, "fg3m": 3.2, "tov": 3.5, "gp": 47, "team": "LAL"},
    "kyrie irving":             {"pts": 25.0, "reb": 5.0, "ast": 5.2, "stl": 1.3, "blk": 0.5, "fg3m": 2.5, "tov": 2.5, "gp": 30, "team": "DAL"},
    "p.j. washington":          {"pts": 14.0, "reb": 6.5, "ast": 2.0, "stl": 1.0, "blk": 0.8, "fg3m": 2.0, "tov": 1.2, "gp": 60, "team": "DAL"},
    "daniel gafford":           {"pts": 12.0, "reb": 7.0, "ast": 1.0, "stl": 0.5, "blk": 2.0, "fg3m": 0.0, "tov": 1.2, "gp": 58, "team": "DAL"},
    "klay thompson":            {"pts": 16.0, "reb": 3.5, "ast": 2.5, "stl": 0.8, "blk": 0.5, "fg3m": 3.5, "tov": 1.2, "gp": 58, "team": "DAL"},

    # ── Denver Nuggets ───────────────────────────────────────────────────────
    "nikola jokic":             {"pts": 26.5, "reb": 12.7, "ast": 10.5, "stl": 1.4, "blk": 0.8, "fg3m": 1.2, "tov": 3.0, "gp": 55, "team": "DEN"},
    "jamal murray":             {"pts": 21.5, "reb": 4.5, "ast": 6.5, "stl": 1.0, "blk": 0.4, "fg3m": 2.5, "tov": 2.5, "gp": 52, "team": "DEN"},
    "michael porter jr.":       {"pts": 17.5, "reb": 7.0, "ast": 1.5, "stl": 0.7, "blk": 0.5, "fg3m": 2.8, "tov": 1.5, "gp": 55, "team": "DEN"},
    "aaron gordon":             {"pts": 13.5, "reb": 6.5, "ast": 3.5, "stl": 0.9, "blk": 0.7, "fg3m": 1.2, "tov": 1.5, "gp": 60, "team": "DEN"},
    "kentavious caldwell-pope": {"pts": 11.0, "reb": 3.0, "ast": 2.0, "stl": 1.0, "blk": 0.3, "fg3m": 2.5, "tov": 1.0, "gp": 58, "team": "DEN"},

    # ── Detroit Pistons ──────────────────────────────────────────────────────
    "cade cunningham":          {"pts": 24.5, "reb": 4.5, "ast": 9.0, "stl": 1.2, "blk": 0.5, "fg3m": 2.0, "tov": 3.5, "gp": 60, "team": "DET"},
    "jalen duren":              {"pts": 14.0, "reb": 11.5, "ast": 2.0, "stl": 0.8, "blk": 1.5, "fg3m": 0.0, "tov": 1.8, "gp": 58, "team": "DET"},
    "ausar thompson":           {"pts": 14.0, "reb": 6.5, "ast": 2.5, "stl": 1.5, "blk": 0.8, "fg3m": 0.8, "tov": 1.5, "gp": 55, "team": "DET"},
    "bojan bogdanovic":         {"pts": 13.0, "reb": 3.0, "ast": 1.5, "stl": 0.5, "blk": 0.2, "fg3m": 2.8, "tov": 1.2, "gp": 50, "team": "DET"},

    # ── Golden State Warriors ────────────────────────────────────────────────
    "stephen curry":            {"pts": 26.0, "reb": 4.5, "ast": 6.2, "stl": 0.9, "blk": 0.3, "fg3m": 4.5, "tov": 2.8, "gp": 40, "team": "GSW"},
    "draymond green":           {"pts": 9.0,  "reb": 7.0, "ast": 6.5, "stl": 1.2, "blk": 0.8, "fg3m": 0.5, "tov": 2.5, "gp": 55, "team": "GSW"},
    "jonathan kuminga":         {"pts": 19.5, "reb": 5.0, "ast": 2.5, "stl": 0.9, "blk": 0.5, "fg3m": 1.5, "tov": 2.0, "gp": 58, "team": "GSW"},
    "buddy hield":              {"pts": 13.5, "reb": 3.0, "ast": 2.0, "stl": 0.8, "blk": 0.2, "fg3m": 3.5, "tov": 1.2, "gp": 60, "team": "GSW"},
    "brandin podziemski":       {"pts": 11.0, "reb": 5.0, "ast": 3.5, "stl": 1.0, "blk": 0.3, "fg3m": 1.8, "tov": 1.5, "gp": 62, "team": "GSW"},

    # ── Houston Rockets ──────────────────────────────────────────────────────
    "alperen sengun":           {"pts": 21.5, "reb": 9.5, "ast": 5.0, "stl": 1.0, "blk": 1.5, "fg3m": 0.5, "tov": 2.8, "gp": 60, "team": "HOU"},
    "jalen green":              {"pts": 22.0, "reb": 4.5, "ast": 4.0, "stl": 1.0, "blk": 0.4, "fg3m": 2.5, "tov": 2.5, "gp": 58, "team": "HOU"},
    "amen thompson":            {"pts": 15.0, "reb": 8.0, "ast": 4.0, "stl": 1.5, "blk": 0.8, "fg3m": 0.5, "tov": 2.0, "gp": 60, "team": "HOU"},
    "dillon brooks":            {"pts": 14.0, "reb": 3.5, "ast": 2.5, "stl": 1.2, "blk": 0.4, "fg3m": 2.0, "tov": 1.5, "gp": 58, "team": "HOU"},
    "fred vanvleet":            {"pts": 14.5, "reb": 3.5, "ast": 6.5, "stl": 1.2, "blk": 0.3, "fg3m": 2.2, "tov": 2.0, "gp": 55, "team": "HOU"},

    # ── Indiana Pacers ───────────────────────────────────────────────────────
    "tyrese haliburton":        {"pts": 18.5, "reb": 3.5, "ast": 9.5, "stl": 1.2, "blk": 0.3, "fg3m": 2.5, "tov": 2.2, "gp": 55, "team": "IND"},
    "pascal siakam":            {"pts": 21.0, "reb": 7.5, "ast": 4.0, "stl": 1.0, "blk": 0.7, "fg3m": 1.5, "tov": 2.0, "gp": 58, "team": "IND"},
    "myles turner":             {"pts": 14.5, "reb": 7.0, "ast": 1.5, "stl": 0.7, "blk": 2.5, "fg3m": 2.0, "tov": 1.5, "gp": 60, "team": "IND"},
    "andrew nembhard":          {"pts": 13.0, "reb": 3.5, "ast": 5.5, "stl": 1.0, "blk": 0.3, "fg3m": 1.8, "tov": 1.8, "gp": 62, "team": "IND"},
    "bennedict mathurin":       {"pts": 17.0, "reb": 4.5, "ast": 2.0, "stl": 0.8, "blk": 0.4, "fg3m": 2.0, "tov": 1.5, "gp": 55, "team": "IND"},

    # ── Los Angeles Clippers ─────────────────────────────────────────────────
    "kawhi leonard":            {"pts": 22.5, "reb": 6.0, "ast": 3.5, "stl": 1.5, "blk": 0.5, "fg3m": 1.8, "tov": 2.0, "gp": 40, "team": "LAC"},
    "paul george":              {"pts": 22.0, "reb": 5.5, "ast": 4.0, "stl": 1.4, "blk": 0.4, "fg3m": 2.5, "tov": 2.5, "gp": 45, "team": "LAC"},
    "james harden":             {"pts": 18.0, "reb": 4.5, "ast": 7.5, "stl": 1.0, "blk": 0.5, "fg3m": 2.5, "tov": 3.5, "gp": 55, "team": "LAC"},
    "ivica zubac":              {"pts": 12.0, "reb": 9.5, "ast": 1.5, "stl": 0.5, "blk": 1.5, "fg3m": 0.0, "tov": 1.5, "gp": 60, "team": "LAC"},
    "norman powell":            {"pts": 16.5, "reb": 3.0, "ast": 2.0, "stl": 0.8, "blk": 0.3, "fg3m": 2.5, "tov": 1.2, "gp": 58, "team": "LAC"},

    # ── Los Angeles Lakers ───────────────────────────────────────────────────
    "lebron james":             {"pts": 23.5, "reb": 7.5, "ast": 9.0, "stl": 1.2, "blk": 0.6, "fg3m": 1.8, "tov": 3.5, "gp": 50, "team": "LAL"},
    "anthony davis":            {"pts": 24.5, "reb": 11.5, "ast": 3.5, "stl": 1.2, "blk": 2.0, "fg3m": 0.5, "tov": 2.0, "gp": 55, "team": "LAL"},
    "austin reaves":            {"pts": 16.0, "reb": 4.0, "ast": 5.5, "stl": 1.0, "blk": 0.3, "fg3m": 2.5, "tov": 1.8, "gp": 62, "team": "LAL"},
    "d'angelo russell":         {"pts": 14.5, "reb": 3.0, "ast": 6.0, "stl": 0.8, "blk": 0.2, "fg3m": 2.5, "tov": 2.0, "gp": 52, "team": "LAL"},
    "rui hachimura":            {"pts": 13.0, "reb": 4.5, "ast": 1.5, "stl": 0.7, "blk": 0.5, "fg3m": 1.5, "tov": 1.2, "gp": 55, "team": "LAL"},

    # ── Memphis Grizzlies ────────────────────────────────────────────────────
    "ja morant":                {"pts": 22.0, "reb": 5.0, "ast": 8.0, "stl": 1.0, "blk": 0.4, "fg3m": 1.0, "tov": 3.0, "gp": 30, "team": "MEM"},
    "jaren jackson jr.":        {"pts": 22.5, "reb": 5.5, "ast": 2.0, "stl": 0.8, "blk": 2.8, "fg3m": 2.5, "tov": 2.0, "gp": 58, "team": "MEM"},
    "desmond bane":             {"pts": 20.0, "reb": 4.5, "ast": 4.0, "stl": 1.0, "blk": 0.3, "fg3m": 3.0, "tov": 1.8, "gp": 55, "team": "MEM"},
    "santi aldama":             {"pts": 13.5, "reb": 6.5, "ast": 2.5, "stl": 0.8, "blk": 1.0, "fg3m": 2.0, "tov": 1.5, "gp": 58, "team": "MEM"},
    "marcus smart":             {"pts": 11.5, "reb": 3.5, "ast": 5.5, "stl": 1.5, "blk": 0.4, "fg3m": 1.5, "tov": 2.0, "gp": 50, "team": "MEM"},

    # ── Miami Heat ───────────────────────────────────────────────────────────
    "jimmy butler":             {"pts": 20.0, "reb": 5.8, "ast": 5.0, "stl": 1.5, "blk": 0.4, "fg3m": 0.5, "tov": 2.0, "gp": 30, "team": "MIA"},
    "tyler herro":              {"pts": 23.5, "reb": 5.0, "ast": 4.5, "stl": 1.0, "blk": 0.3, "fg3m": 3.0, "tov": 2.5, "gp": 55, "team": "MIA"},
    "bam adebayo":              {"pts": 19.5, "reb": 10.0, "ast": 4.0, "stl": 1.2, "blk": 0.8, "fg3m": 0.2, "tov": 2.2, "gp": 58, "team": "MIA"},
    "terry rozier":             {"pts": 18.0, "reb": 4.0, "ast": 4.5, "stl": 1.2, "blk": 0.3, "fg3m": 2.5, "tov": 2.0, "gp": 55, "team": "MIA"},
    "nikola jovic":             {"pts": 11.0, "reb": 5.0, "ast": 2.5, "stl": 0.7, "blk": 0.5, "fg3m": 1.5, "tov": 1.2, "gp": 58, "team": "MIA"},

    # ── Milwaukee Bucks ──────────────────────────────────────────────────────
    "giannis antetokounmpo":    {"pts": 30.5, "reb": 11.5, "ast": 5.8, "stl": 1.1, "blk": 1.5, "fg3m": 0.7, "tov": 3.4, "gp": 50, "team": "MIL"},
    "damian lillard":           {"pts": 25.0, "reb": 4.0, "ast": 7.0, "stl": 0.9, "blk": 0.3, "fg3m": 3.2, "tov": 2.8, "gp": 55, "team": "MIL"},
    "khris middleton":          {"pts": 14.5, "reb": 4.5, "ast": 4.0, "stl": 0.8, "blk": 0.3, "fg3m": 1.8, "tov": 1.8, "gp": 45, "team": "MIL"},
    "brook lopez":              {"pts": 12.5, "reb": 5.5, "ast": 2.0, "stl": 0.5, "blk": 2.0, "fg3m": 2.0, "tov": 1.2, "gp": 58, "team": "MIL"},
    "bobby portis":             {"pts": 13.5, "reb": 7.5, "ast": 1.5, "stl": 0.7, "blk": 0.5, "fg3m": 1.8, "tov": 1.5, "gp": 55, "team": "MIL"},

    # ── Minnesota Timberwolves ───────────────────────────────────────────────
    "anthony edwards":          {"pts": 26.0, "reb": 5.5, "ast": 5.2, "stl": 1.5, "blk": 0.5, "fg3m": 2.5, "tov": 2.8, "gp": 55, "team": "MIN"},
    "karl-anthony towns":       {"pts": 24.0, "reb": 13.0, "ast": 3.0, "stl": 0.9, "blk": 1.1, "fg3m": 2.8, "tov": 2.5, "gp": 58, "team": "NYK"},
    "rudy gobert":              {"pts": 13.5, "reb": 12.0, "ast": 1.5, "stl": 0.8, "blk": 2.0, "fg3m": 0.0, "tov": 1.5, "gp": 60, "team": "MIN"},
    "mike conley":              {"pts": 10.0, "reb": 3.0, "ast": 5.5, "stl": 1.0, "blk": 0.2, "fg3m": 2.0, "tov": 1.5, "gp": 52, "team": "MIN"},
    "jaden mcdaniels":          {"pts": 15.5, "reb": 4.5, "ast": 2.0, "stl": 1.2, "blk": 1.0, "fg3m": 2.0, "tov": 1.2, "gp": 58, "team": "MIN"},

    # ── New Orleans Pelicans ─────────────────────────────────────────────────
    "zion williamson":          {"pts": 22.5, "reb": 5.5, "ast": 4.5, "stl": 1.0, "blk": 0.6, "fg3m": 0.3, "tov": 2.5, "gp": 42, "team": "NOP"},
    "brandon ingram":           {"pts": 21.5, "reb": 5.0, "ast": 5.5, "stl": 0.8, "blk": 0.5, "fg3m": 2.0, "tov": 2.2, "gp": 45, "team": "NOP"},
    "cj mccollum":              {"pts": 17.5, "reb": 4.0, "ast": 5.0, "stl": 0.9, "blk": 0.3, "fg3m": 2.8, "tov": 2.0, "gp": 52, "team": "NOP"},
    "jonas valanciunas":        {"pts": 12.0, "reb": 9.5, "ast": 2.0, "stl": 0.5, "blk": 0.8, "fg3m": 0.5, "tov": 1.5, "gp": 55, "team": "NOP"},

    # ── New York Knicks ──────────────────────────────────────────────────────
    "jalen brunson":            {"pts": 27.5, "reb": 3.5, "ast": 7.5, "stl": 0.9, "blk": 0.2, "fg3m": 2.5, "tov": 2.8, "gp": 60, "team": "NYK"},
    "julius randle":            {"pts": 20.5, "reb": 8.5, "ast": 4.5, "stl": 0.9, "blk": 0.4, "fg3m": 2.0, "tov": 2.5, "gp": 55, "team": "NYK"},
    "og anunoby":               {"pts": 15.5, "reb": 5.5, "ast": 2.0, "stl": 1.5, "blk": 0.8, "fg3m": 2.2, "tov": 1.2, "gp": 50, "team": "NYK"},
    "mikal bridges":            {"pts": 17.5, "reb": 4.5, "ast": 3.5, "stl": 1.0, "blk": 0.5, "fg3m": 2.0, "tov": 1.5, "gp": 62, "team": "NYK"},
    "josh hart":                {"pts": 12.0, "reb": 8.5, "ast": 4.0, "stl": 1.2, "blk": 0.3, "fg3m": 1.5, "tov": 1.5, "gp": 65, "team": "NYK"},

    # ── Oklahoma City Thunder ────────────────────────────────────────────────
    "shai gilgeous-alexander":  {"pts": 31.5, "reb": 5.5, "ast": 6.0, "stl": 2.0, "blk": 1.0, "fg3m": 1.5, "tov": 2.2, "gp": 55, "team": "OKC"},
    "jalen williams":           {"pts": 23.0, "reb": 4.5, "ast": 5.5, "stl": 1.3, "blk": 0.5, "fg3m": 1.8, "tov": 2.2, "gp": 26, "team": "OKC"},
    "chet holmgren":            {"pts": 17.5, "reb": 7.5, "ast": 2.5, "stl": 0.8, "blk": 2.5, "fg3m": 2.0, "tov": 1.5, "gp": 58, "team": "OKC"},
    "lu dort":                  {"pts": 13.5, "reb": 4.0, "ast": 2.5, "stl": 1.5, "blk": 0.5, "fg3m": 2.5, "tov": 1.2, "gp": 60, "team": "OKC"},
    "isaiah joe":               {"pts": 10.5, "reb": 2.5, "ast": 1.5, "stl": 0.8, "blk": 0.2, "fg3m": 2.8, "tov": 0.8, "gp": 58, "team": "OKC"},

    # ── Orlando Magic ────────────────────────────────────────────────────────
    "paolo banchero":           {"pts": 25.5, "reb": 7.5, "ast": 5.5, "stl": 1.0, "blk": 0.8, "fg3m": 1.8, "tov": 3.0, "gp": 58, "team": "ORL"},
    "franz wagner":             {"pts": 22.0, "reb": 5.5, "ast": 4.5, "stl": 1.0, "blk": 0.5, "fg3m": 2.0, "tov": 2.0, "gp": 60, "team": "ORL"},
    "wendell carter jr.":       {"pts": 12.0, "reb": 8.5, "ast": 2.5, "stl": 0.7, "blk": 1.2, "fg3m": 0.5, "tov": 1.5, "gp": 55, "team": "ORL"},
    "jalen suggs":              {"pts": 14.0, "reb": 4.0, "ast": 5.0, "stl": 1.5, "blk": 0.5, "fg3m": 1.8, "tov": 2.0, "gp": 58, "team": "ORL"},
    "markelle fultz":           {"pts": 12.5, "reb": 3.5, "ast": 5.5, "stl": 1.0, "blk": 0.3, "fg3m": 0.8, "tov": 1.8, "gp": 50, "team": "ORL"},

    # ── Philadelphia 76ers ───────────────────────────────────────────────────
    "joel embiid":              {"pts": 28.0, "reb": 10.0, "ast": 3.5, "stl": 0.8, "blk": 1.6, "fg3m": 1.5, "tov": 3.0, "gp": 20, "team": "PHI"},
    "tyrese maxey":             {"pts": 25.5, "reb": 3.5, "ast": 6.0, "stl": 1.0, "blk": 0.4, "fg3m": 2.8, "tov": 2.5, "gp": 58, "team": "PHI"},
    "paul george":              {"pts": 19.5, "reb": 5.0, "ast": 3.5, "stl": 1.2, "blk": 0.4, "fg3m": 2.5, "tov": 2.0, "gp": 40, "team": "PHI"},
    "tobias harris":            {"pts": 15.0, "reb": 6.5, "ast": 3.0, "stl": 0.7, "blk": 0.5, "fg3m": 1.8, "tov": 1.5, "gp": 55, "team": "PHI"},
    "kelly oubre jr.":          {"pts": 14.5, "reb": 5.0, "ast": 1.5, "stl": 1.2, "blk": 0.5, "fg3m": 2.0, "tov": 1.5, "gp": 52, "team": "PHI"},

    # ── Phoenix Suns ─────────────────────────────────────────────────────────
    "kevin durant":             {"pts": 27.2, "reb": 6.8, "ast": 4.5, "stl": 0.7, "blk": 1.2, "fg3m": 2.0, "tov": 2.5, "gp": 55, "team": "PHX"},
    "devin booker":             {"pts": 25.5, "reb": 4.2, "ast": 6.5, "stl": 0.9, "blk": 0.3, "fg3m": 2.2, "tov": 2.5, "gp": 45, "team": "PHX"},
    "bradley beal":             {"pts": 17.5, "reb": 4.0, "ast": 5.0, "stl": 0.8, "blk": 0.3, "fg3m": 1.5, "tov": 2.5, "gp": 42, "team": "PHX"},
    "jusuf nurkic":             {"pts": 11.0, "reb": 10.0, "ast": 3.5, "stl": 0.8, "blk": 1.0, "fg3m": 0.2, "tov": 2.0, "gp": 52, "team": "PHX"},
    "grayson allen":            {"pts": 13.5, "reb": 3.5, "ast": 2.5, "stl": 0.8, "blk": 0.3, "fg3m": 3.2, "tov": 1.0, "gp": 58, "team": "PHX"},

    # ── Portland Trail Blazers ───────────────────────────────────────────────
    "anfernee simons":          {"pts": 21.0, "reb": 3.5, "ast": 5.0, "stl": 0.9, "blk": 0.3, "fg3m": 3.0, "tov": 2.2, "gp": 55, "team": "POR"},
    "jerami grant":             {"pts": 18.5, "reb": 4.5, "ast": 2.5, "stl": 1.0, "blk": 0.8, "fg3m": 2.0, "tov": 1.5, "gp": 52, "team": "POR"},
    "deandre ayton":            {"pts": 14.5, "reb": 9.5, "ast": 1.5, "stl": 0.5, "blk": 0.8, "fg3m": 0.0, "tov": 1.8, "gp": 48, "team": "POR"},
    "scoot henderson":          {"pts": 14.0, "reb": 3.5, "ast": 6.0, "stl": 1.0, "blk": 0.3, "fg3m": 1.2, "tov": 2.5, "gp": 58, "team": "POR"},

    # ── Sacramento Kings ─────────────────────────────────────────────────────
    "domantas sabonis":         {"pts": 19.0, "reb": 13.5, "ast": 8.0, "stl": 1.0, "blk": 0.5, "fg3m": 0.5, "tov": 3.0, "gp": 60, "team": "SAC"},
    "de'aaron fox":             {"pts": 23.5, "reb": 4.5, "ast": 7.5, "stl": 1.5, "blk": 0.4, "fg3m": 1.5, "tov": 3.0, "gp": 55, "team": "SAC"},
    "keegan murray":            {"pts": 16.5, "reb": 5.0, "ast": 2.0, "stl": 0.8, "blk": 0.5, "fg3m": 2.5, "tov": 1.2, "gp": 60, "team": "SAC"},
    "harrison barnes":          {"pts": 12.5, "reb": 5.5, "ast": 2.0, "stl": 0.8, "blk": 0.5, "fg3m": 1.8, "tov": 1.2, "gp": 58, "team": "SAC"},

    # ── San Antonio Spurs ────────────────────────────────────────────────────
    "victor wembanyama":        {"pts": 24.5, "reb": 10.5, "ast": 3.5, "stl": 1.2, "blk": 3.5, "fg3m": 2.5, "tov": 2.5, "gp": 60, "team": "SAS"},
    "devin vassell":            {"pts": 18.5, "reb": 4.0, "ast": 3.5, "stl": 1.2, "blk": 0.5, "fg3m": 2.5, "tov": 1.5, "gp": 55, "team": "SAS"},
    "keldon johnson":           {"pts": 14.0, "reb": 5.0, "ast": 2.5, "stl": 1.0, "blk": 0.4, "fg3m": 1.8, "tov": 1.5, "gp": 58, "team": "SAS"},
    "chris paul":               {"pts": 8.5,  "reb": 3.5, "ast": 7.5, "stl": 1.2, "blk": 0.2, "fg3m": 0.8, "tov": 2.0, "gp": 45, "team": "SAS"},
    "stephon castle":           {"pts": 13.0, "reb": 3.5, "ast": 3.5, "stl": 1.2, "blk": 0.4, "fg3m": 1.2, "tov": 1.8, "gp": 58, "team": "SAS"},

    # ── Toronto Raptors ──────────────────────────────────────────────────────
    "scottie barnes":           {"pts": 20.5, "reb": 8.5, "ast": 6.0, "stl": 1.3, "blk": 0.8, "fg3m": 1.5, "tov": 2.5, "gp": 55, "team": "TOR"},
    "rg norms barrett":         {"pts": 15.5, "reb": 5.0, "ast": 3.0, "stl": 0.8, "blk": 0.4, "fg3m": 2.0, "tov": 1.5, "gp": 58, "team": "TOR"},
    "immanuel quickley":        {"pts": 17.5, "reb": 4.5, "ast": 6.5, "stl": 1.0, "blk": 0.3, "fg3m": 2.5, "tov": 2.0, "gp": 55, "team": "TOR"},
    "kelly olynyk":             {"pts": 10.5, "reb": 6.0, "ast": 3.5, "stl": 0.6, "blk": 0.5, "fg3m": 1.8, "tov": 1.5, "gp": 50, "team": "TOR"},

    # ── Utah Jazz ────────────────────────────────────────────────────────────
    "lauri markkanen":          {"pts": 23.5, "reb": 8.5, "ast": 2.0, "stl": 0.7, "blk": 0.5, "fg3m": 2.8, "tov": 1.8, "gp": 55, "team": "UTA"},
    "collin sexton":            {"pts": 18.5, "reb": 3.0, "ast": 4.5, "stl": 0.9, "blk": 0.2, "fg3m": 2.0, "tov": 1.8, "gp": 58, "team": "UTA"},
    "john collins":             {"pts": 14.5, "reb": 7.5, "ast": 2.0, "stl": 0.7, "blk": 0.8, "fg3m": 1.5, "tov": 1.5, "gp": 52, "team": "UTA"},
    "jordan clarkson":          {"pts": 15.0, "reb": 3.5, "ast": 3.5, "stl": 0.8, "blk": 0.2, "fg3m": 2.2, "tov": 1.8, "gp": 55, "team": "UTA"},
    "walker kessler":           {"pts": 10.0, "reb": 9.5, "ast": 1.5, "stl": 0.5, "blk": 2.8, "fg3m": 0.0, "tov": 1.2, "gp": 58, "team": "UTA"},

    # ── Washington Wizards ───────────────────────────────────────────────────
    "kyle kuzma":               {"pts": 18.5, "reb": 7.5, "ast": 4.0, "stl": 0.8, "blk": 0.5, "fg3m": 2.0, "tov": 2.0, "gp": 52, "team": "WAS"},
    "tyus jones":               {"pts": 11.5, "reb": 3.0, "ast": 8.5, "stl": 1.2, "blk": 0.2, "fg3m": 1.2, "tov": 1.8, "gp": 58, "team": "WAS"},
    "deni avdija":              {"pts": 16.5, "reb": 6.5, "ast": 4.5, "stl": 1.2, "blk": 0.5, "fg3m": 1.5, "tov": 1.8, "gp": 55, "team": "WAS"},
    "bilal coulibaly":          {"pts": 13.5, "reb": 4.5, "ast": 2.5, "stl": 1.5, "blk": 0.8, "fg3m": 1.5, "tov": 1.2, "gp": 58, "team": "WAS"},
    "alexandre sarr":           {"pts": 12.0, "reb": 6.5, "ast": 1.5, "stl": 0.8, "blk": 1.8, "fg3m": 1.2, "tov": 1.5, "gp": 55, "team": "WAS"},
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


# ── source 1: nba_api LeagueLeaders ──────────────────────────────────────────

def _fetch_all_player_stats_bulk(season: str) -> dict[int, PlayerStats] | None:
    global _bulk_cache, _bulk_tried, _bulk_cache_ts, _nba_api_dead

    if _bulk_tried and _bulk_cache is not None:
        if (time.time() - _bulk_cache_ts) < CACHE_TTL_SECONDS:
            return _bulk_cache
        else:
            _bulk_tried = False
            _nba_api_dead = False

    if _bulk_tried:
        return _bulk_cache
    _bulk_tried = True

    if _nba_api_dead:
        return None

    try:
        from nba_api.stats.endpoints import leagueleaders

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                leaders = leagueleaders.LeagueLeaders(
                    season=season,
                    stat_category_abbreviation="PTS",
                    per_mode48="PerGame",
                    headers=CUSTOM_HEADERS,
                    timeout=API_TIMEOUT,
                )
                break
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    logger.warning("LeagueLeaders attempt %d/%d failed: %s — retrying in %ds...", attempt + 1, MAX_RETRIES, e, RETRY_DELAY)
                    time.sleep(RETRY_DELAY)
                else:
                    raise last_error

        frames = leaders.get_data_frames()
        if not frames or frames[0] is None or frames[0].empty:
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
        _bulk_cache_ts = time.time()
        logger.info("LeagueLeaders: fetched live stats for %d players", len(cache))
        return cache

    except Exception as e:
        logger.warning("LeagueLeaders failed after %d attempts: %s", MAX_RETRIES, e)
        _nba_api_dead = True
        return None


# ── source 2: per-player fallback ────────────────────────────────────────────

def _try_single_player(player_id: int, season: str) -> PlayerStats | None:
    global _nba_api_dead
    if _nba_api_dead:
        return None
    try:
        from nba_api.stats.endpoints import playergamelog
        log = playergamelog.PlayerGameLog(
            player_id=player_id, season=season,
            season_type_all_star="Regular Season",
            headers=CUSTOM_HEADERS, timeout=API_TIMEOUT,
        )
        frames = log.get_data_frames()
        if not frames or frames[0] is None or frames[0].empty:
            return PlayerStats(player_id=player_id, player_name=_resolve_name(player_id), games_played=0, season=season)
        df = frames[0]
        matchup = df.iloc[0].get("MATCHUP", "") if "MATCHUP" in df.columns else ""
        return PlayerStats(
            player_id=player_id, player_name=_resolve_name(player_id),
            team_abbreviation=matchup.split()[0] if matchup else None,
            games_played=int(len(df)),
            minutes_per_game=_safe_mean(df, "MIN"), points_per_game=_safe_mean(df, "PTS"),
            rebounds_per_game=_safe_mean(df, "REB"), assists_per_game=_safe_mean(df, "AST"),
            steals_per_game=_safe_mean(df, "STL"), blocks_per_game=_safe_mean(df, "BLK"),
            fg3m_per_game=_safe_mean(df, "FG3M"), tov_per_game=_safe_mean(df, "TOV"),
            fg_pct=_safe_mean(df, "FG_PCT"), fg3_pct=_safe_mean(df, "FG3_PCT"),
            ft_pct=_safe_mean(df, "FT_PCT"), season=season,
        )
    except Exception as e:
        logger.warning("nba_api single-player %s: %s", player_id, e)
        _nba_api_dead = True
        return None


# ── source 3: sample data ────────────────────────────────────────────────────

def _from_sample(player_id: int, player_name: str, season: str) -> PlayerStats:
    sample = _SAMPLE_STATS.get(player_name.lower()) or _SAMPLE_STATS.get(_ascii(player_name).lower())
    if sample:
        logger.info("Using sample data for %s", player_name)
    else:
        logger.warning("No stats found for '%s' — using zeroed fallback. Player may not exist or NBA API is down.", player_name)
    return PlayerStats(
        player_id=player_id, player_name=player_name,
        team_abbreviation=sample["team"] if sample else "UNK",
        games_played=sample["gp"] if sample else 0,
        points_per_game=sample["pts"] if sample else 0.0,
        rebounds_per_game=sample["reb"] if sample else 0.0,
        assists_per_game=sample["ast"] if sample else 0.0,
        steals_per_game=sample["stl"] if sample else 0.0,
        blocks_per_game=sample["blk"] if sample else 0.0,
        fg3m_per_game=sample["fg3m"] if sample else 0.0,
        tov_per_game=sample["tov"] if sample else 0.0,
        season=season,
    )


# ── public API ───────────────────────────────────────────────────────────────

def get_player_season_stats(player_id: int, season: str | None = None) -> PlayerStats | None:
    season = season or _current_season()
    player_name = _resolve_name(player_id)
    if _bulk_cache and player_id in _bulk_cache:
        return _bulk_cache[player_id]
    if not _nba_api_dead:
        result = _try_single_player(player_id, season)
        if result:
            return result
    return _from_sample(player_id, player_name, season)


def get_players_season_stats(player_ids: list[int], season: str | None = None) -> dict[int, PlayerStats]:
    """Fetch season stats for multiple players.

    Cascade: LeagueLeaders bulk → sample data (skips per-player if API is dead).
    """
    season = season or _current_season()
    result: dict[int, PlayerStats] = {}

    bulk = _fetch_all_player_stats_bulk(season)
    if bulk:
        for pid in player_ids:
            if pid in bulk:
                result[pid] = bulk[pid]
        missing = [pid for pid in player_ids if pid not in result]
        if not missing:
            return result
        player_ids = missing

    for i, pid in enumerate(player_ids):
        stats = None
        if not _nba_api_dead:
            if i > 0:
                time.sleep(0.8)
            stats = _try_single_player(pid, season)
        if stats:
            result[pid] = stats
        else:
            result[pid] = _from_sample(pid, _resolve_name(pid), season)

    return result