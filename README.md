# EZLineup

NBA fantasy lineup optimizer that selects the best players within a salary budget using real season stats, live injury data, and an AI-powered assistant.

**[Try it live → ezlineup.streamlit.app](https://ezlineup.streamlit.app/)**

## How It Works

1. Search for NBA players by name (auto-completed from the live NBA roster) and assign each a salary cost
2. Set a salary budget and optionally tweak per-stat scoring multipliers (PTS, REB, AST, STL, BLK, 3PM, TOV)
3. The backend fetches current season stats via the NBA API (LeagueLeaders endpoint)
4. Injury data is fetched via the **Tavily** web search API — injured players are excluded or their scores penalized
5. A position-constrained optimizer selects exactly one player per slot (PG/SG/SF/PF/C) to maximize total projected fantasy score within the budget
6. Results are displayed with scores, injury status, and a plain-English explanation
7. A built-in **Gemini-powered NBA assistant** (sidebar chat) lets you ask questions about your lineup, injuries, or strategy

## Features

- **Live player search** — autocomplete from the full active NBA roster via `nba_api` PlayerIndex
- **Player headshots** — NBA CDN photos shown inline when selecting or viewing players
- **Position lineup board** — visual 5-slot board (PG/SG/SF/PF/C) showing your roster organized by position
- **Customizable scoring** — sliders to tune each stat multiplier before optimizing
- **Injury awareness** — Out players excluded, Questionable players penalized ~25%; injury report browsable separately
- **Batch add** — paste a list of `Name, Cost` lines to add multiple players at once
- **AI assistant** — Gemini 2.0 Flash chatbot aware of your current roster and optimized lineup
- **Standalone injury browser** — fetch and search the full NBA injury report without running an optimization

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | Streamlit |
| **Backend** | Python (imported directly, no separate server required) |
| **NBA Data** | `nba_api` — PlayerIndex, LeagueLeaders |
| **Injury Data** | Tavily web search API |
| **AI Assistant** | Google Gemini 2.0 Flash (`google-genai`) |
| **Optimization** | Custom position-constrained combinatorial optimizer |
| **Models / Validation** | Pydantic v2 |
| **LangChain tools** | LangChain (tools wrappers, RAG service scaffold) |

## Prerequisites

- **Python 3.10+**
- **Tavily API key** *(optional)* — needed for live injury reports. Free tier available at [tavily.com](https://tavily.com). Set `TAVILY_API_KEY` in your environment or a `.env` file. The app works without it; injury data will be unavailable.
- **Gemini API key** *(optional)* — needed for the AI assistant. Free tier available at [ai.google.dev](https://ai.google.dev). Set `GEMINI_API_KEY` in your environment or a `.env` file.

## Setup & Run

**1. Clone the repo:**

```bash
git clone <repo-url>
cd EZLineup
```

**2. Create and activate a virtual environment:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**3. Install dependencies:**

```bash
pip install -r streamlit_app/requirements.txt
```

**4. (Optional) Add API keys:**

Create a `.env` file in the project root:

```
TAVILY_API_KEY=your_tavily_key_here
GEMINI_API_KEY=your_gemini_key_here
```

**5. Run the Streamlit app:**

```bash
python3 -m streamlit run streamlit_app/app.py
```

The app opens at `http://localhost:8501`. Add players with costs, set a budget, and click **Optimize Lineup**.

**6. (Optional) Run the FastAPI backend separately:**

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Then test the API at `http://localhost:8000/health`.

## Project Structure

```
EZLineup/
├── streamlit_app/
│   ├── requirements.txt              # All Python dependencies
│   ├── app.py                        # Full Streamlit UI (roster entry, optimization, results, chat)
│   ├── components/                   # (placeholder for extracted UI components)
│   └── utils/                        # (placeholder for API client utilities)
│
└── backend/
    ├── requirements.txt              # Backend-only dependencies
    └── app/
        ├── main.py                   # FastAPI app entry point, CORS, route registration
        ├── models/
        │   ├── lineup.py             # Request/response models (roster, scoring rules, results)
        │   └── player.py             # PlayerStats model (per-game averages)
        ├── routes/
        │   ├── health.py             # GET /health endpoint
        │   └── lineup.py             # POST /api/lineup and GET /api/injuries endpoints
        ├── services/
        │   ├── nba_service.py        # NBA data fetching (PlayerIndex → LeagueLeaders → sample fallback)
        │   ├── injury_service.py     # Fetches injury status via Tavily web search
        │   ├── lineup_service.py     # Orchestrates: resolve IDs → fetch stats → injuries → optimize
        │   ├── optimizer.py          # Position-constrained combinatorial optimizer with injury penalties
        │   ├── scoring.py            # Projects fantasy scores from season averages
        │   ├── fantasy_scorer.py     # Core scoring engine with configurable multipliers
        │   └── rag_service.py        # RAG service scaffold for league rules/strategy context
        ├── tools/
        │   ├── nba_tools.py          # LangChain tools wrapping NBA stats/schedule lookups
        │   ├── calculator_tool.py    # Math expression evaluator tool
        │   └── datetime_tool.py      # Current date/time tool
        ├── agents/                   # (placeholder for future LangChain agent)
        └── tests/
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health check |
| POST | `/api/lineup` | Optimize lineup from roster + budget |
| GET | `/api/injuries` | Current NBA injury report |

## Data Sources

| Source | What | Notes |
|--------|------|-------|
| `nba_api` PlayerIndex | Active roster with positions | Used for player search autocomplete |
| `nba_api` LeagueLeaders | Season per-game stats for all players | Primary stats source |
| `nba_api` PlayerGameLog | Per-player game log | Fallback (often rate-limited) |
| Tavily web search | Live injury status per roster player | Requires `TAVILY_API_KEY`; free tier available |
| Sample data | Hardcoded stats for ~20 popular players | Last-resort fallback |

## Team

Khushi Gauli, Sravya Kotamraju, Nancy Nakyung Kwak, Ousman Bah
