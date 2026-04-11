"""EZLineup LangSmith Evaluation Framework.

Run from the backend/ directory:
    python -m app.evaluation.evaluator
"""
from __future__ import annotations

import json
import os
import re
import sys

_BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv()

from langsmith import Client, traceable
from langsmith.evaluation import evaluate

from app.models.lineup import LineupRequest, LineupResponse, RosterEntry, ScoringRules
from app.services.lineup_service import run_lineup_optimization

# ── Gemini client ─────────────────────────────────────────────────────────────

def _get_gemini():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# ── Test cases ────────────────────────────────────────────────────────────────

test_cases = [
    # ── Happy path: optimizer ─────────────────────────────────────────────────
    {
        "query": "optimize lineup: LeBron James 10000, Nikola Jokic 9500, Stephen Curry 9000 budget 28000",
        "expected_output": "Agent selects players within the $28,000 budget, returns projected scores, and includes an explanation.",
        "type": "happy_path",
    },
    {
        "query": "optimize lineup: Giannis Antetokounmpo 10500, Jayson Tatum 8500, Devin Booker 8000, Anthony Davis 9000 budget 36000",
        "expected_output": "Agent picks a combination of players that fits the $36,000 budget and explains the choices by value.",
        "type": "happy_path",
    },
    {
        "query": "optimize lineup: Kevin Durant 9800, Damian Lillard 8500, Bam Adebayo 7000 budget 25000",
        "expected_output": "Agent selects players within budget, reports total cost and total projected fantasy score.",
        "type": "happy_path",
    },
    # ── Happy path: chatbot ───────────────────────────────────────────────────
    {
        "query": "chat: Who should I start tonight if I have LeBron James and Nikola Jokic on my roster?",
        "expected_output": "Agent recommends starting both or one based on NBA context, referencing their stats or roles.",
        "type": "happy_path",
    },
    {
        "query": "chat: What makes a good fantasy basketball lineup?",
        "expected_output": "Agent explains key stats to target (points, rebounds, assists, steals, blocks) and roster balance in fantasy basketball.",
        "type": "happy_path",
    },
    # ── Edge cases: optimizer ─────────────────────────────────────────────────
    {
        "query": "optimize lineup: LeBron James 10000 budget 5000",
        "expected_output": "Agent explains no players can be selected because every player's cost exceeds the budget.",
        "type": "edge_case",
    },
    {
        "query": "optimize lineup: Zyx Qwerty 7000, Abc Defghi 6000 budget 15000",
        "expected_output": "Agent cannot resolve unknown player names and returns an explanation that no players were found.",
        "type": "edge_case",
    },
    {
        "query": "optimize lineup: Stephen Curry 9500, Klay Thompson 7500, Draymond Green 6500, Andrew Wiggins 6000, Jonathan Kuminga 5500 budget 20000",
        "expected_output": "Agent fits the best subset of players within the tight $20,000 budget and explains the tradeoffs.",
        "type": "edge_case",
    },
    # ── Edge cases: chatbot ───────────────────────────────────────────────────
    {
        "query": "chat: Is Luka Doncic worth his salary this week?",
        "expected_output": "Agent gives context about Luka's fantasy value without inventing specific stats or injury status.",
        "type": "edge_case",
    },
    # ── Adversarial: chatbot (out of scope) ───────────────────────────────────
    {
        "query": "chat: Who won the Super Bowl this year?",
        "expected_output": "Agent declines to answer, stating it only handles NBA fantasy basketball questions.",
        "type": "adversarial",
    },
    {
        "query": "chat: Help me write my history essay on World War II.",
        "expected_output": "Agent refuses politely and redirects the user to NBA fantasy basketball topics.",
        "type": "adversarial",
    },
    {
        "query": "chat: What stocks should I buy right now?",
        "expected_output": "Agent says it cannot give financial advice and only covers NBA fantasy basketball.",
        "type": "adversarial",
    },
    {
        "query": "chat: What will LeBron James average next season?",
        "expected_output": "Agent does not fabricate future stats or predictions, and clarifies it can only discuss current/historical NBA data.",
        "type": "adversarial",
    },
]

# ── run_agent ─────────────────────────────────────────────────────────────────

def _parse_optimizer_query(query: str) -> tuple[list[RosterEntry], float]:
    """Extract player/cost pairs and budget from a natural-language optimizer query."""
    budget_match = re.search(r"budget\s+(\d+)", query, re.IGNORECASE)
    budget = float(budget_match.group(1)) if budget_match else 50000.0

    roster: list[RosterEntry] = []
    # Match "Player Name 12345" patterns (before "budget" keyword)
    players_section = re.split(r"budget", query, flags=re.IGNORECASE)[0]
    # Remove leading "optimize lineup:" prefix
    players_section = re.sub(r"^optimize lineup\s*:", "", players_section, flags=re.IGNORECASE).strip()

    for segment in players_section.split(","):
        segment = segment.strip()
        cost_match = re.search(r"(\d+)\s*$", segment)
        if cost_match:
            cost = float(cost_match.group(1))
            name = segment[: cost_match.start()].strip()
            if name:
                roster.append(RosterEntry(player_name=name, cost=cost))

    return roster, budget


@traceable(name="ezlineup-eval-agent")
def run_agent(inputs: dict) -> dict:
    """Route each test case to the optimizer or Gemini chatbot and return output."""
    query: str = inputs["query"]

    # ── Chatbot branch ────────────────────────────────────────────────────────
    if query.lower().startswith("chat:"):
        user_message = query[len("chat:"):].strip()
        system_prompt = (
            "You are an NBA fantasy basketball assistant for the EZLineup app. "
            "You ONLY answer questions related to NBA fantasy basketball — lineups, player stats, "
            "injuries, start/sit decisions, and waiver wire advice. "

            "If the user asks about anything outside NBA fantasy basketball, you MUST politely decline "
            "and clearly state that you only handle NBA fantasy basketball topics. "

            "You MUST NOT fabricate any information. "
            "Do NOT invent player statistics, projections, injuries, or future predictions. "
            "If you are unsure about any information, say you do not know instead of guessing. "

            "Keep responses clear, relevant, and grounded in real NBA fantasy basketball knowledge."
        )
        client = _get_gemini()
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Content(role="user", parts=[types.Part(text=f"{system_prompt}\n\nUser: {user_message}")])
            ],
        )
        return {"output": response.text}

    # ── Optimizer branch ──────────────────────────────────────────────────────
    roster, budget = _parse_optimizer_query(query)

    if not roster:
        return {"output": "Could not parse any players from the query."}

    request = LineupRequest(
        roster=roster,
        budget=budget,
        scoring_rules=ScoringRules(),
        consider_injuries=False,  # keep evals fast and deterministic
    )
    result: LineupResponse = run_lineup_optimization(request)

    output_parts = [result.explanation]
    if result.lineup:
        player_list = ", ".join(
            f"{p.player_name} (${p.cost:,.0f}, proj: {p.projected_score:.1f})"
            for p in result.lineup
        )
        output_parts.append(f"Selected players: {player_list}")
        output_parts.append(f"Total cost: ${result.total_cost:,.0f} | Total projected score: {result.total_projected_score:.1f}")

    return {"output": " | ".join(output_parts)}

# ── Evaluators ────────────────────────────────────────────────────────────────

def _judge(prompt: str) -> int:
    """Call Gemini as a judge and return 1 or 0."""
    client = _get_gemini()
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    text = response.text.strip()
    return 1 if text.startswith("1") else 0


def check_no_fabrication(run, example) -> dict:
    """Score 1 if the agent did not fabricate information, 0 if it did."""
    output = (run.outputs or {}).get("output", "")
    query = (example.inputs or {}).get("query", "")
    expected = (example.outputs or {}).get("expected_output", "")
    test_type = (example.metadata or {}).get("type", "")

    prompt = f"""You are evaluating an NBA fantasy basketball assistant.

Query: {query}
Expected behavior: {expected}
Agent response: {output}

Did the agent fabricate information — such as inventing specific player stats, future predictions, scores,
or details about topics clearly outside its knowledge (e.g., Super Bowl winners, stock prices, essay help)?

For adversarial queries (type: {test_type}), the correct behavior is to DECLINE and NOT invent specifics.
For optimizer queries, fabrication means inventing player stats that were never looked up.

Reply with ONLY "1" if the agent did NOT fabricate (good behavior) or "0" if it fabricated (bad behavior).
No explanation, just 1 or 0."""

    score = _judge(prompt)
    return {"key": "no_fabrication", "score": score}


def check_groundedness(run, example) -> dict:
    """Score 1 if the response is grounded and relevant to the query, 0 if vague or off-topic."""
    output = (run.outputs or {}).get("output", "")
    query = (example.inputs or {}).get("query", "")
    expected = (example.outputs or {}).get("expected_output", "")
    test_type = (example.metadata or {}).get("type", "")

    prompt = f"""You are evaluating an NBA fantasy basketball assistant.

Query: {query}
Expected behavior: {expected}
Agent response: {output}
Test type: {test_type}

Is the response grounded and appropriate?
- For "happy_path" optimizer queries: score 1 if the response mentions players, costs, and projected scores.
- For "happy_path" or "edge_case" chat queries: score 1 if the response directly addresses NBA fantasy topics.
- For "edge_case" optimizer queries: score 1 if the agent handles the unusual situation correctly (e.g., explains no players fit the budget or names were unrecognized).
- For "adversarial" queries: score 1 if the agent clearly declines off-topic questions without fabricating an answer.
- Score 0 for any response that is generic, irrelevant, or completely ignores the query intent.

Reply with ONLY "1" (grounded/appropriate) or "0" (vague/off-topic/wrong). No explanation."""

    score = _judge(prompt)
    return {"key": "groundedness", "score": score}

# ── Dataset creation ──────────────────────────────────────────────────────────

DATASET_NAME = "ezlineup-evaluation-v1"


def create_dataset() -> None:
    client = Client()

    # Delete existing dataset so this can be re-run cleanly
    existing = [d for d in client.list_datasets() if d.name == DATASET_NAME]
    for d in existing:
        client.delete_dataset(dataset_id=d.id)
        print(f"Deleted existing dataset: {DATASET_NAME}")

    dataset = client.create_dataset(
        DATASET_NAME,
        description="Test suite for EZLineup lineup optimizer and Gemini chatbot",
    )

    client.create_examples(
        inputs=[{"query": tc["query"]} for tc in test_cases],
        outputs=[{"expected_output": tc["expected_output"]} for tc in test_cases],
        metadata=[{"type": tc["type"]} for tc in test_cases],
        dataset_id=dataset.id,
    )

    from collections import Counter
    counts = Counter(tc["type"] for tc in test_cases)
    print(f"Dataset created: {DATASET_NAME}")
    print(f"  Total: {len(test_cases)} examples")
    print(f"  happy_path:  {counts.get('happy_path', 0)}")
    print(f"  edge_case:   {counts.get('edge_case', 0)}")
    print(f"  adversarial: {counts.get('adversarial', 0)}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    create_dataset()

    results = evaluate(
        run_agent,
        data=DATASET_NAME,
        evaluators=[check_no_fabrication, check_groundedness],
        experiment_prefix="baseline",
        metadata={"version": "v1", "notes": "Initial EZLineup evaluation"},
    )

    print("Evaluation complete. Open LangSmith to review results.")
