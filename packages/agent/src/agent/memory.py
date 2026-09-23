"""Short-term (Redis) and long-term (Chroma) memory for the Q&A agent.

Short-term: the last N (question, answer) turns for a chat session, so a
follow-up like "what about the median" can actually see the prior turn
instead of relying on the schema alone to guess the right column (the
Phase 3 limitation documented in CLAUDE.md). Stored in Redis, not
process memory, specifically so it survives an API server restart - the
`ChatSession`/`ChatMessage` rows in Postgres are the permanent record, but
they're not indexed for "give me the last N turns fast" the way a capped
Redis list is, and Postgres is not what "session state" means in the
Phase 6 checklist (a Postgres row is durable no matter what; the point
being tested is that server-restart recovery doesn't depend on
in-process state).

Long-term: corrections/preferences a user has stated about a dataset
("region means store region, not country"), embedded and stored in
Chroma, retrieved by (user_id, dataset_id) plus semantic similarity to
the current question. This is genuinely persistent across sessions -
that's the whole point of it being long-term.
"""

import json
import uuid
from datetime import datetime, timezone

from chromadb.utils import embedding_functions

from core.storage import get_chroma_client, get_redis_client

_SHORT_TERM_MAX_TURNS = 6
_SHORT_TERM_TTL_SECONDS = 24 * 60 * 60  # 1 day of inactivity - a cache, not the record of truth
_LONG_TERM_COLLECTION = "user_memory"
_LONG_TERM_TOP_K = 5
# Cosine distance (0 = identical, 2 = opposite) - facts beyond this are almost
# certainly unrelated to the current question and would just add prompt noise.
_LONG_TERM_MAX_DISTANCE = 1.2


def _turns_key(session_id: str) -> str:
    return f"session:{session_id}:turns"


def append_turn(session_id: str, question: str, answer: str, route: str) -> None:
    redis = get_redis_client()
    key = _turns_key(session_id)
    turn = json.dumps({"question": question, "answer": answer, "route": route})
    pipe = redis.pipeline()
    pipe.rpush(key, turn)
    pipe.ltrim(key, -_SHORT_TERM_MAX_TURNS, -1)
    pipe.expire(key, _SHORT_TERM_TTL_SECONDS)
    pipe.execute()


def get_recent_turns(session_id: str | None) -> list[dict]:
    if not session_id:
        return []
    redis = get_redis_client()
    raw = redis.lrange(_turns_key(session_id), 0, -1)
    return [json.loads(t) for t in raw]


def render_recent_turns(turns: list[dict]) -> str:
    if not turns:
        return ""
    lines = [f'Q: {t["question"]}\nA: {t["answer"]}' for t in turns]
    return "\n\n".join(lines)


_embedding_function = None


def _get_embedding_function():
    global _embedding_function
    if _embedding_function is None:
        _embedding_function = embedding_functions.DefaultEmbeddingFunction()
    return _embedding_function


def _get_collection():
    client = get_chroma_client()
    return client.get_or_create_collection(
        _LONG_TERM_COLLECTION, embedding_function=_get_embedding_function(), metadata={"hnsw:space": "cosine"}
    )


def remember_fact(user_id: str, dataset_id: str, fact: str) -> str:
    fact_id = str(uuid.uuid4())
    _get_collection().add(
        documents=[fact],
        ids=[fact_id],
        metadatas=[
            {
                "user_id": user_id,
                "dataset_id": dataset_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )
    return fact_id


def retrieve_facts(user_id: str, dataset_id: str, query: str, k: int = _LONG_TERM_TOP_K) -> list[str]:
    collection = _get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_texts=[query],
        n_results=k,
        where={"$and": [{"user_id": user_id}, {"dataset_id": dataset_id}]},
    )
    docs = result.get("documents") or [[]]
    distances = result.get("distances") or [[]]
    return [doc for doc, dist in zip(docs[0], distances[0]) if dist <= _LONG_TERM_MAX_DISTANCE]


def render_facts(facts: list[str]) -> str:
    if not facts:
        return ""
    return "\n".join(f"- {f}" for f in facts)
