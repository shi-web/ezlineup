# EZLineup 🏀 
### Season‑Focused NBA Lineup Optimization Agent (MVP) 
EZLineup is a lightweight, season‑focused AI agent that helps users build smarter NBA lineups. Instead of integrating with fantasy platforms, users manually enter their roster and optional budget. The agent retrieves real NBA season data, evaluates players using a simple scoring model, builds an optimal lineup, and explains *why* it made each decision. This MVP is designed to be beginner‑friendly. 

--- 

## 🚀 Features (MVP) 
- Manual roster + budget input 
- NBA season data retrieval 
- Simple scoring model (minutes, usage, matchup, injuries) 
- Budget‑aware lineup optimization 
- Beginner‑friendly explanations 
- Streamlit UI + FastAPI backend 
- LangChain agent for reasoning and explanation

---

## 📊 How It Works
1. User enters roster + budget
2. Backend fetches NBA season stats
3. Scoring model evaluates each player
4. Optimizer builds best lineup under constraints
5. LangChain agent generates explanations
6. Streamlit displays lineup + reasoning

---

## 📌 Future Improvements
- More advanced scoring model
- Player comparison tool
- Injury trend analysis
- Optional platform integrations
- Better visualization of stats

---

## Tech Stack

- **Backend**: FastAPI + LangChain (Python)
- **Frontend**: Streamlit
- **LLM**: TBD
- **Data Sources**: NBA stats APIs, injury feeds, and more

---

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
## Team 
Khushi Gauli, Sravya Kotamraju, Nancy Kwak, Ousman Bah