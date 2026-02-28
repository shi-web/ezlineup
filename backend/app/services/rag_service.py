"""RAG service: index league rules and strategy docs, expose retrieval for the agent."""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# Default league rules and strategy for NBA fantasy (semi-static knowledge)
DEFAULT_LEAGUE_RULES = """
Standard NBA fantasy league rules (default):
- Roster: typically 10-13 players with position requirements (PG, SG, SF, PF, C).
- Scoring: points (1), rebounds (1.2), assists (1.5), steals (3), blocks (3). Minutes indicate usage.
- Budget: total salary/cost of selected players must not exceed the user's budget.
- Lineup lock: lineups lock at first game of the day; check current date for lock times.
"""

STRATEGY_DOCS = """
How we interpret stats for lineup decisions:
- Minutes per game: primary proxy for usage and opportunity; higher minutes usually mean more fantasy value.
- Points, rebounds, assists: core counting stats; we weight assists and stocks (steals, blocks) slightly higher.
- Shooting efficiency (FG%, 3P%, FT%): used as a small multiplier; efficient players get a slight boost.
- Matchup and injuries: not available in MVP; assume full availability. Use web search for late-breaking news.
- Value per cost: we recommend players with the best projected score per dollar of cost under the user's budget.
"""


def _get_embedding():
    """Lazy load embeddings to avoid slow startup."""
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except ImportError:
        from langchain_core.embeddings import FakeEmbeddings
        return FakeEmbeddings(size=384)


def _default_documents() -> list[Document]:
    return [
        Document(page_content=DEFAULT_LEAGUE_RULES, metadata={"source": "default_rules"}),
        Document(page_content=STRATEGY_DOCS, metadata={"source": "strategy"}),
    ]


class RAGService:
    """In-memory RAG for league rules and strategy. Optional user rules added at query time."""

    def __init__(self):
        self._embedding = _get_embedding()
        self._splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        docs = _default_documents()
        chunks = self._splitter.split_documents(docs)
        self._store = InMemoryVectorStore.from_documents(chunks, self._embedding)

    def add_league_rules(self, rules_text: str) -> None:
        """Add user-provided league rules to the store for this session."""
        if not rules_text or not rules_text.strip():
            return
        docs = [Document(page_content=rules_text.strip(), metadata={"source": "user_rules"})]
        chunks = self._splitter.split_documents(docs)
        self._store.add_documents(chunks)

    def query(self, question: str, k: int = 3) -> str:
        """Retrieve relevant chunks for the question. Returns concatenated context string."""
        try:
            found = self._store.similarity_search(question, k=k)
            return "\n\n".join(d.page_content for d in found)
        except Exception as e:
            logger.warning("RAG query failed: %s", e)
            return DEFAULT_LEAGUE_RULES + "\n\n" + STRATEGY_DOCS


# Singleton for app-wide use; optional user rules can be passed per request
_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
