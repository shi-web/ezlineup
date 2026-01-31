# EZLineup 🏀 

### Season‑Focused NBA Lineup Optimization Agent (MVP)

An AI-powered NBA fantasy lineup recommendation agent.

## What It Does

EZLineup helps fantasy basketball players build optimal lineups by automatically gathering player stats, monitoring injuries, and providing personalized recommendations. It's designed to be beginner-friendly while handling the heavy lifting of manual research.

## Tech Stack

- **Backend**: FastAPI + LangChain (Python)
- **Frontend**: Streamlit
- **LLM**: TBD
- **Data Sources**: NBA stats APIs, injury feeds, and more

## Project Structure

```
EZLineup/
├── backend/
│   ├── app/
│   │   ├── agents/          # LangChain agent logic
│   │   ├── routes/          # API endpoints
│   │   ├── services/        # Business logic (data fetching, optimization, scoring)
│   │   ├── models/          # Data models
│   │   └── utils/           # Helper functions
│   └── tests/
│
└── streamlit_app/
    ├── app.py               # Main UI
    ├── components/          # Reusable UI components
    └── utils/               # API client and helpers
```

---

**Team**: Khushi Gauli, Sravya Kotamraju, Nancy Kwak, Ousman Bah