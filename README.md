# EZLineup

NBA fantasy lineup optimizer that selects the best players within a salary budget using real stats and live injury data.

## How It Works

1. User enters a roster of players with salary costs and a budget
2. Backend fetches season stats via the NBA API (LeagueLeaders endpoint)
3. Each player is scored using weighted fantasy multipliers (PTS, REB, AST, STL, BLK, FG3M, TOV)
4. Injury data is fetched via the `nbainjuries` package — injured players are excluded or penalized
5. A greedy optimizer selects the highest-value players that fit within the budget
6. Results are displayed in the Streamlit UI with scores, injury status, and an explanation

## Tech Stack

- **Backend:** FastAPI, Pydantic, nba_api, nbainjuries, LangChain (tools/RAG)
- **Frontend:** Streamlit
- **Data:** NBA LeagueLeaders API, NBA official injury reports

## Prerequisites

- **Python 3.10+**
- **Java Runtime (optional)** — needed only for live injury reports via the `nbainjuries` package. Check with `java -version`. The app works without it; injury data will just be unavailable.

## Setup & Run

**1. Clone the repo and navigate into it:**

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
pip install -r backend/requirements.txt -r streamlit_app/requirements.txt
```

**4. Run the Streamlit app:**

```bash
streamlit run streamlit_app/app.py
```

The app opens at `http://localhost:8501`. Add players with costs, set a budget, and click **Optimize Lineup**.

**5. (Optional) Run the FastAPI backend separately:**

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Then test the API at `http://localhost:8000/health`.

## Project Structure

```
EZLineup/
├── backend/
│   ├── requirements.txt              # Python dependencies for backend
│   ├── app/
│   │   ├── main.py                   # FastAPI app entry point, CORS, route registration
│   │   ├── models/
│   │   │   ├── lineup.py             # Request/response models (roster, scoring rules, results)
│   │   │   └── player.py             # PlayerStats model (per-game averages)
│   │   ├── routes/
│   │   │   ├── health.py             # GET /health endpoint
│   │   │   └── lineup.py             # POST /api/lineup and GET /api/injuries endpoints
│   │   ├── services/
│   │   │   ├── nba_service.py        # NBA data fetching (LeagueLeaders → PlayerGameLog → sample data)
│   │   │   ├── injury_service.py     # Fetches NBA injury reports via nbainjuries package
│   │   │   ├── lineup_service.py     # Orchestrates: resolve IDs → fetch stats → injuries → optimize
│   │   │   ├── optimizer.py          # Greedy budget-constrained lineup selection with injury penalties
│   │   │   ├── scoring.py            # Projects fantasy scores from season averages
│   │   │   ├── fantasy_scorer.py     # Core scoring engine with configurable multipliers
│   │   │   └── rag_service.py        # RAG service for league rules/strategy context
│   │   ├── tools/
│   │   │   ├── nba_tools.py          # LangChain tools wrapping NBA stats/schedule lookups
│   │   │   ├── calculator_tool.py    # Math expression evaluator tool
│   │   │   └── datetime_tool.py      # Current date/time tool
│   │   ├── agents/                   # (placeholder for future LangChain agent)
│   │   └── utils/
│   └── tests/
│
└── streamlit_app/
    ├── requirements.txt              # Python dependencies for frontend
    ├── app.py                        # Full Streamlit UI (roster entry, optimization, results, injury browser)
    ├── components/                   # (placeholder for extracted UI components)
    └── utils/                        # (placeholder for API client utilities)
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health check |
| POST | `/api/lineup` | Optimize lineup from roster + budget |
| GET | `/api/injuries` | Current NBA injury report |

## Data Sources

| Source | What | Reliability |
|--------|------|-------------|
| `nba_api` LeagueLeaders | Season per-game stats for all NBA players | High (single fast call) |
| `nba_api` PlayerGameLog | Per-player game log fallback | Low (often blocked) |
| `nbainjuries` | Official NBA injury reports | Requires Java runtime |
| Sample data | Hardcoded stats for ~20 popular players | Always available |

## Team

Khushi Gauli, Sravya Kotamraju, Nancy Kwak, Ousman Bah
