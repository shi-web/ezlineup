"""EZLineup – NBA Lineup Optimizer with Injury Intelligence.

Run:  streamlit run streamlit_app/app.py
(from the project root)
"""
from __future__ import annotations

import os
import sys
import time

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Make backend importable (so we can call services directly without running
# the FastAPI server separately).
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_SCRIPT_DIR, "..", "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.models.lineup import LineupRequest, RosterEntry, ScoringRules  # noqa: E402
from app.services.injury_service import fetch_injury_report  # noqa: E402
from app.services.lineup_service import run_lineup_optimization  # noqa: E402

# ── Position slot definitions (label + beginner-friendly tooltip) ─────────────
POSITION_SLOTS: dict[str, dict] = {
    "PG": {
        "label": "Point Guard",
        "tooltip": "Usually the smallest player on the court. Handles the ball a lot and passes to teammates. Earns fantasy points mainly through assists and steals.",
    },
    "SG": {
        "label": "Shooting Guard",
        "tooltip": "A guard who focuses more on scoring. Good for racking up points and 3-pointers in fantasy.",
    },
    "SF": {
        "label": "Small Forward",
        "tooltip": "A mid-sized player who can score, rebound, and defend. Does a little of everything — great for fantasy.",
    },
    "PF": {
        "label": "Power Forward",
        "tooltip": "A bigger forward who plays near the basket. Earns fantasy points through rebounds and blocks.",
    },
    "C": {
        "label": "Center",
        "tooltip": "The tallest player, stays near the basket. Best for rebounds and blocks in fantasy.",
    },
}

# Maps raw NBA API position strings → specific fantasy slot
_API_POS_TO_SLOT: dict[str, str] = {
    "G": "PG", "PG": "PG", "SG": "SG",
    "G-F": "SG", "F-G": "SG",
    "F": "SF", "SF": "SF", "PF": "PF",
    "F-C": "PF", "C-F": "PF",
    "C": "C",
}


def _api_pos_to_slot(raw: str) -> str:
    return _API_POS_TO_SLOT.get(raw.upper().strip(), raw or "?")


@st.cache_data(show_spinner=False, ttl=3600 * 6)
def _load_player_data() -> dict[str, dict]:
    """Return {full_name: {id, position}} for all active NBA players.

    Tries PlayerIndex (positions + IDs) → static list (IDs only) → sample fallback.
    """
    try:
        from nba_api.stats.endpoints import playerindex
        pi = playerindex.PlayerIndex(league_id="00", timeout=30)
        df = pi.get_data_frames()[0]
        result: dict[str, dict] = {}
        for _, row in df.iterrows():
            if row.get("ROSTER_STATUS") != 1:
                continue
            name = f"{row['PLAYER_FIRST_NAME']} {row['PLAYER_LAST_NAME']}".strip()
            result[name] = {
                "id": int(row["PERSON_ID"]),
                "position": _api_pos_to_slot(str(row.get("POSITION", ""))),
            }
        if result:
            return result
    except Exception:
        pass
    # Fallback: static list has IDs but no positions
    try:
        from nba_api.stats.static import players as static_players
        return {
            p["full_name"]: {"id": p["id"], "position": "?"}
            for p in static_players.get_active_players()
        }
    except Exception:
        pass
    # Last resort: hardcoded sample data names, no IDs
    from app.services.nba_service import _SAMPLE_STATS
    return {name.title(): {"id": None, "position": "?"} for name in _SAMPLE_STATS}

# ── Gemini setup ─────────────────────────────────────────────────────────────
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

_gemini_model = None


def _get_gemini_client():
    global _gemini_model
    if _gemini_model is None:
        try:
            from google import genai
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                return None
            _gemini_model = genai.Client(api_key=api_key)
        except ImportError:
            return None
    return _gemini_model


def _build_system_context() -> str:
    """Build context string from current roster and lineup results."""
    parts = [
        "You are an expert NBA fantasy basketball assistant inside the EZLineup app.",
        "Be concise, helpful, and conversational. Use basketball knowledge to give smart advice.",
        "Keep responses short and to the point — 2-4 sentences unless a detailed breakdown is asked for.",
    ]

    roster = st.session_state.get("roster", [])
    if roster:
        roster_str = ", ".join(f"{p['name']} (${p['cost']:,.0f})" for p in roster)
        parts.append(f"\nThe user's current roster: {roster_str}")
    else:
        parts.append("\nThe user has not added any players to their roster yet.")

    results = st.session_state.get("results")
    if results and results.lineup:
        lineup_str = ", ".join(
            f"{p.player_name} (proj: {p.projected_score:.1f}, {p.injury_status or 'Healthy'})"
            for p in results.lineup
        )
        parts.append(f"\nOptimized lineup: {lineup_str}")
        parts.append(f"Total projected score: {results.total_projected_score:.1f}")
        parts.append(f"Optimizer explanation: {results.explanation}")

        injured = [p for p in results.lineup if p.injury_status and p.injury_status not in ("Available", None)]
        if injured:
            inj_str = ", ".join(f"{p.player_name} ({p.injury_status})" for p in injured)
            parts.append(f"Players with injury concerns: {inj_str}")

    return "\n".join(parts)


def _chat_with_gemini(user_message: str, history: list[dict]) -> str:
    client = _get_gemini_client()
    if client is None:
        return "⚠️ GEMINI_API_KEY not set in .env or google-genai not installed."
    try:
        from google.genai import types
        system_ctx = _build_system_context()

        # Build conversation history
        contents = []
        for msg in history[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

        # Inject system context on first message
        final_message = f"{system_ctx}\n\nUser question: {user_message}"

        contents.append(types.Content(role="user", parts=[types.Part(text=final_message)]))

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=contents,
        )
        return response.text
    except Exception as e:
        return f"⚠️ Gemini error: {e}"


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="EZLineup", page_icon="🏀", layout="wide")

# ── Custom styles ────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; }
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border: 1px solid #0f3460;
        border-radius: 10px;
        padding: 14px 18px;
        box-shadow: 0 2px 8px rgba(0,0,0,.2);
    }
    div[data-testid="stMetric"] label {
        font-size: 0.85rem;
        color: #a8b2d1 !important;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-weight: 700;
    }
    /* Right chat panel styling */
    .chat-panel {
        border-left: 1px solid #0f3460;
        padding-left: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state ────────────────────────────────────────────────────────────
if "roster" not in st.session_state:
    st.session_state.roster: list[dict] = []
if "results" not in st.session_state:
    st.session_state.results = None
if "injury_cache" not in st.session_state:
    st.session_state.injury_cache = None
if "injury_cache_ts" not in st.session_state:
    st.session_state.injury_cache_ts = 0.0
if "chat_history" not in st.session_state:
    st.session_state.chat_history: list[dict] = []
if "add_form_key" not in st.session_state:
    st.session_state.add_form_key = 0


def _add_player(name: str, cost: float, position: str = "?", player_id: int | None = None) -> None:
    name = name.strip()
    if name:
        st.session_state.roster.append({
            "name": name,
            "cost": cost,
            "position": position,
            "player_id": player_id,
        })


def _remove_player(idx: int) -> None:
    if 0 <= idx < len(st.session_state.roster):
        st.session_state.roster.pop(idx)


def _clear_roster() -> None:
    st.session_state.roster = []
    st.session_state.results = None


def _get_injuries_cached(players: list[str], ttl: int = 1800) -> list[dict]:
    """Return cached injury report, refreshing after *ttl* seconds."""
    now = time.time()
    if (
        st.session_state.injury_cache is None
        or now - st.session_state.injury_cache_ts > ttl
    ):
        st.session_state.injury_cache = fetch_injury_report(players)
        st.session_state.injury_cache_ts = now
    return st.session_state.injury_cache


# ── Sidebar – Settings ───────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    budget = st.number_input(
        "💰 Salary Budget",
        min_value=0,
        max_value=500_000,
        value=50_000,
        step=1_000,
        help="Maximum total salary for selected players",
    )

    consider_injuries = st.toggle("🏥 Factor in Injuries", value=True)
    if consider_injuries:
        st.caption(
            "Players listed *Out* are excluded. *Questionable* scores are reduced ~25%."
        )

    st.divider()

    with st.expander("📊 Scoring Multipliers"):
        st.caption("Tweak how each stat category contributes to the fantasy score.")
        pts_m = st.slider("Points", 0.0, 5.0, 1.0, 0.1, key="pts_m")
        reb_m = st.slider("Rebounds", 0.0, 5.0, 1.2, 0.1, key="reb_m")
        ast_m = st.slider("Assists", 0.0, 5.0, 1.5, 0.1, key="ast_m")
        stl_m = st.slider("Steals", 0.0, 5.0, 3.0, 0.1, key="stl_m")
        blk_m = st.slider("Blocks", 0.0, 5.0, 3.0, 0.1, key="blk_m")
        fg3_m = st.slider("3-Pointers Made", 0.0, 5.0, 0.5, 0.1, key="fg3_m")
        tov_m = st.slider("Turnovers", -5.0, 0.0, -1.0, 0.1, key="tov_m")

    st.divider()
    st.caption(
        "EZLineup v0.1 — stats via [nba_api](https://github.com/swar/nba_api) "
        "LeagueLeaders | injuries via [Tavily](https://tavily.com)"
    )

# ── Top-level layout: main content + right chat panel ────────────────────────
col_main, col_chat = st.columns([3, 1], gap="large")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT COLUMN — NBA Assistant chatbot
# ══════════════════════════════════════════════════════════════════════════════
with col_chat:
    st.markdown("<div class='chat-panel'>", unsafe_allow_html=True)
    st.subheader("🤖 NBA Assistant")
    st.caption("Ask about your lineup, injuries, or anything NBA.")

    # Chat history display
    chat_container = st.container(height=500)
    with chat_container:
        if not st.session_state.chat_history:
            st.markdown(
                "<div style='color:#a8b2d1; font-size:0.85rem; text-align:center; padding-top:100px;'>"
                "💬 Ask me anything!<br><br>"
                "<i>Why was this player picked?<br>"
                "Who should I start tonight?<br>"
                "Explain my lineup.</i>"
                "</div>",
                unsafe_allow_html=True,
            )
        for msg in st.session_state.chat_history:
            if msg["role"] == "user":
                st.chat_message("user").write(msg["content"])
            else:
                st.chat_message("assistant").write(msg["content"])

    user_input = st.chat_input("Ask about your lineup...", key="chat_input")
    if user_input:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        with st.spinner("Thinking..."):
            reply = _chat_with_gemini(user_input, st.session_state.chat_history)
        st.session_state.chat_history.append({"role": "model", "content": reply})
        st.rerun()

    if st.session_state.chat_history:
        if st.button("🗑️ Clear chat", use_container_width=True, key="clear_chat"):
            st.session_state.chat_history = []
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# LEFT/MAIN COLUMN — all existing app content
# ══════════════════════════════════════════════════════════════════════════════
with col_main:
    # ── Header ───────────────────────────────────────────────────────────────
    st.title("🏀 EZLineup")
    st.markdown(
        "Build optimal NBA fantasy lineups powered by **real season stats** "
        "and **live injury data**."
    )
    st.divider()

    # ── Two-column layout: add players + roster ───────────────────────────────
    col_form, col_roster = st.columns([2, 3], gap="large")

    # ---- Add players ----
    with col_form:
        st.subheader("Add Players")

        _player_data = _load_player_data()
        _existing = {p["name"].lower() for p in st.session_state.roster}
        _player_names = sorted(k for k in _player_data if k.lower() not in _existing)

        _name_input = st.selectbox(
            "Player name",
            options=_player_names,
            format_func=lambda n: f"{n}  ·  {_player_data[n]['position']}",
            index=None,
            placeholder="Type to search…",
            key=f"player_select_{st.session_state.add_form_key}",
        )

        # Live player card — updates immediately when a name is selected
        _pos_override = "?"
        _selected_id = None
        if _name_input:
            _pdata = _player_data[_name_input]
            _selected_id = _pdata.get("id")
            card_c1, card_c2 = st.columns([1, 3])
            with card_c1:
                if _selected_id:
                    st.markdown(
                        f'<img src="https://cdn.nba.com/headshots/nba/latest/260x190/{_selected_id}.png" '
                        f'width="75" style="border-radius:8px;" '
                        f'onerror="this.style.display=\'none\'">',
                        unsafe_allow_html=True,
                    )
            with card_c2:
                st.markdown(f"**{_name_input}**")
                _slot_options = list(POSITION_SLOTS.keys())
                _default_idx = _slot_options.index(_pdata["position"]) if _pdata["position"] in _slot_options else 0
                _pos_override = st.selectbox(
                    "Position",
                    options=_slot_options,
                    index=_default_idx,
                    help="Auto-detected from NBA API. Override if incorrect.",
                    key=f"pos_override_{st.session_state.add_form_key}",
                )

        c1, c2 = st.columns([2, 1])
        with c1:
            cost_input = st.number_input(
                "Cost ($)", min_value=0, value=5000, step=500,
                key=f"cost_{st.session_state.add_form_key}",
            )
        with c2:
            st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
            _add_clicked = st.button("➕ Add", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

        if _add_clicked:
            if _name_input:
                _add_player(_name_input, float(cost_input), _pos_override, _selected_id)
                st.session_state.add_form_key += 1
                st.rerun()
            else:
                st.warning("Select a player first.")

        st.divider()
        st.caption("**Batch add** — paste one player per line (`Name, Cost`)")
        batch = st.text_area(
            "batch_input",
            placeholder="LeBron James, 10000\nStephen Curry, 9500\nKevin Durant, 9800",
            height=110,
            label_visibility="collapsed",
        )
        if st.button("📥  Add all from text", use_container_width=True):
            added = 0
            for line in batch.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.rsplit(",", 1)
                pname = parts[0].strip()
                pcost = float(parts[1].strip()) if len(parts) > 1 else 0.0
                if pname:
                    # Try to resolve position from loaded data (case-insensitive)
                    _match = next((k for k in _player_data if k.lower() == pname.lower()), None)
                    _batch_pos = _player_data[_match]["position"] if _match else "?"
                    _batch_id = _player_data[_match]["id"] if _match else None
                    _add_player(_match or pname, pcost, _batch_pos, _batch_id)
                    added += 1
            if added:
                st.rerun()

    # ---- Roster ----
    with col_roster:
        st.subheader("Your Roster")

        if not st.session_state.roster:
            st.info("Add players using the form on the left to get started.")
        else:
            header_cols = st.columns([0.5, 2.5, 1.2, 1.5, 0.8])
            header_cols[0].markdown("**#**")
            header_cols[1].markdown("**Player**")
            header_cols[2].markdown("**Position**")
            header_cols[3].markdown("**Cost**")
            header_cols[4].markdown("**Remove**")

            for idx, player in enumerate(st.session_state.roster):
                rc = st.columns([0.5, 2.5, 1.2, 1.5, 0.8])
                rc[0].write(idx + 1)
                rc[1].write(player["name"])
                rc[2].write(player.get("position", "?"))
                rc[3].write(f"${player['cost']:,.0f}")
                if rc[4].button("✕", key=f"rm_{idx}"):
                    _remove_player(idx)
                    st.rerun()

            total_salary = sum(p["cost"] for p in st.session_state.roster)
            st.markdown(
                f"**{len(st.session_state.roster)} players** — "
                f"Total salary: **${total_salary:,.0f}** / ${budget:,.0f}"
            )

            if st.button("🗑️  Clear roster", use_container_width=True):
                _clear_roster()
                st.rerun()

    # ── 5-slot lineup board ───────────────────────────────────────────────────
    if st.session_state.roster:
        st.divider()
        st.subheader("Lineup Board")
        st.caption("Players organized by position. Hover the position labels for details.")
        slot_cols = st.columns(5, gap="small")
        for col, (pos, info) in zip(slot_cols, POSITION_SLOTS.items()):
            with col:
                st.markdown(f"**{pos}**", help=info["tooltip"])
                st.caption(info["label"])
                slot_players = [p for p in st.session_state.roster if p.get("position") == pos]
                if slot_players:
                    for sp in slot_players:
                        pid = sp.get("player_id")
                        if pid:
                            st.markdown(
                                f'<img src="https://cdn.nba.com/headshots/nba/latest/260x190/{pid}.png" '
                                f'width="60" style="border-radius:6px;" '
                                f'onerror="this.style.display=\'none\'">',
                                unsafe_allow_html=True,
                            )
                        st.markdown(
                            f"<span style='font-size:0.8rem'>{sp['name']}</span><br>"
                            f"<span style='font-size:0.75rem; color:#a8b2d1'>${sp['cost']:,.0f}</span>",
                            unsafe_allow_html=True,
                        )
                        st.markdown("<hr style='margin:4px 0; border-color:#0f3460'>", unsafe_allow_html=True)
                else:
                    st.markdown(
                        "<div style='border:2px dashed #0f3460; border-radius:8px; "
                        "height:80px; display:flex; align-items:center; "
                        "justify-content:center; color:#a8b2d1; font-size:0.8rem;'>"
                        "Empty</div>",
                        unsafe_allow_html=True,
                    )

    # ── Optimize button ───────────────────────────────────────────────────────
    st.divider()

    if st.session_state.roster:
        if st.button(
            "🚀  **Optimize Lineup**",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Resolving players, fetching stats & injuries…"):
                try:
                    roster_entries = [
                        RosterEntry(player_name=p["name"], cost=p["cost"], player_id=p.get("player_id"), position=p.get("position", "?"))
                        for p in st.session_state.roster
                    ]

                    scoring = ScoringRules(
                        pts_multiplier=pts_m,
                        reb_multiplier=reb_m,
                        ast_multiplier=ast_m,
                        stl_multiplier=stl_m,
                        blk_multiplier=blk_m,
                        fg3m_multiplier=fg3_m,
                        tov_multiplier=tov_m,
                    )

                    request = LineupRequest(
                        roster=roster_entries,
                        budget=float(budget),
                        scoring_rules=scoring,
                        consider_injuries=consider_injuries,
                    )

                    st.session_state.results = run_lineup_optimization(request)
                except Exception as exc:
                    st.error(f"Optimization failed: {exc}")
    else:
        st.info("Add at least one player to your roster, then click **Optimize Lineup**.")

    # ── Results ───────────────────────────────────────────────────────────────
    if st.session_state.results is not None:
        result = st.session_state.results
        st.divider()
        st.subheader("📊 Optimal Lineup")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Players Selected", len(result.lineup))
        m2.metric("Total Cost", f"${result.total_cost:,.0f}")
        m3.metric("Projected Score", f"{result.total_projected_score:.1f}")
        m4.metric("Budget Remaining", f"${budget - result.total_cost:,.0f}")

        if result.lineup:
            _roster_pos_lookup = {p["name"].lower(): p.get("position", "?") for p in st.session_state.roster}
            rows = []
            for p in result.lineup:
                status_icon = ""
                if p.injury_status:
                    status_icon = {
                        "Out": "🔴",
                        "Doubtful": "🟠",
                        "Questionable": "🟡",
                        "Probable": "🟢",
                    }.get(p.injury_status, "⚪")

                rows.append(
                    {
                        "Player": p.player_name,
                        "Position": _roster_pos_lookup.get(p.player_name.lower(), "?"),
                        "Cost": p.cost,
                        "Proj. Score": round(p.projected_score, 1),
                        "Injury": f"{status_icon} {p.injury_status}" if p.injury_status else "✅ Healthy",
                        "Note": p.injury_note or "",
                    }
                )

            df = pd.DataFrame(rows)
            st.dataframe(
                df.style.format({"Cost": "${:,.0f}", "Proj. Score": "{:.1f}"}),
                use_container_width=True,
                hide_index=True,
            )

            injured_in_lineup = [p for p in result.lineup if p.injury_status and p.injury_status not in ("Available", None)]
            if injured_in_lineup:
                st.warning(
                    f"⚠️ {len(injured_in_lineup)} selected player(s) have injury concerns. "
                    "Their projected scores have been adjusted."
                )
        else:
            st.warning("No players could be selected within the budget. Try increasing the budget or adding different players.")

        with st.expander("💡 Explanation", expanded=True):
            st.write(result.explanation)

        if result.injuries:
            with st.expander(f"🏥 Injury Report ({len(result.injuries)} roster players affected)"):
                inj_rows = []
                for inj in result.injuries:
                    icon = {"Out": "🔴", "Doubtful": "🟠", "Questionable": "🟡"}.get(inj.status, "⚪")
                    inj_rows.append(
                        {
                            "Player": inj.player_name,
                            "Position": _roster_pos_lookup.get(inj.player_name.lower(), "?"),
                            "Status": f"{icon} {inj.status}",
                            "Reason": inj.reason,
                        }
                    )
                st.dataframe(pd.DataFrame(inj_rows), use_container_width=True, hide_index=True)

    # ── Standalone injury browser ─────────────────────────────────────────────
    st.divider()
    with st.expander("🏥 Browse Full NBA Injury Report"):
        st.caption(
            "Live injury data via [Tavily](https://tavily.com) web search. "
            "Requires a valid `TAVILY_API_KEY` in your `.env` file."
        )
        if st.button("🔄  Fetch / Refresh Injuries"):
            with st.spinner("Fetching latest injury report…"):
                roster_names = [p["name"] for p in st.session_state.roster]
                st.session_state.injury_cache = fetch_injury_report(roster_names) if roster_names else []
                st.session_state.injury_cache_ts = time.time()

        cached = st.session_state.injury_cache
        if cached:
            search_term = st.text_input(
                "Filter by player or team name",
                placeholder="e.g. Lakers or LeBron",
                key="inj_search",
            )
            display_injuries = cached
            if search_term:
                term = search_term.lower()
                display_injuries = [
                    r for r in cached
                    if term in r.get("player_name", "")
                    or term in r.get("team", "").lower()
                    or term in r.get("display_name", "").lower()
                ]

            if display_injuries:
                df_inj = pd.DataFrame(
                    [
                        {
                            "Player": r.get("display_name", r["player_name"]),
                            "Status": r["status"],
                            "Reason": r.get("reason", ""),
                        }
                        for r in display_injuries
                    ]
                )
                st.dataframe(df_inj, use_container_width=True, hide_index=True)
                st.caption(f"Showing {len(display_injuries)} of {len(cached)} entries.")
            else:
                st.info("No matching injuries found.")
        elif cached is not None:
            st.info(
                "No injury data available. This may happen during off-season or "
                "if TAVILY_API_KEY is not set."
            )