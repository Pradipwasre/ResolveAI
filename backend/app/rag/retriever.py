"""Hybrid retrieval: vector search + keyword search, fused and reranked.

1. Vector search (pgvector, cosine) finds chunks with similar meaning.
2. Full-text search (Postgres tsvector) finds exact terms like "COD" or "UPI".
3. Reciprocal Rank Fusion merges both lists without needing comparable scores.
4. Optional LLM reranking re-scores the top candidates for precision.
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import RerankScores
from app.config import get_settings
from app.llm import gateway
from app.llm.fake_provider import STOPWORDS
from app.models import KnowledgeChunk

RRF_K = 60


@dataclass
class RetrievedChunk:
    id: int
    doc_id: str
    doc_title: str
    section: str
    content: str
    score: float
    similarity: float = 0.0  # cosine similarity from vector search (0 if only a keyword hit)
    keyword_hit: bool = False


def _keyword_query(question: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", question.lower()) if w not in STOPWORDS]
    return " | ".join(dict.fromkeys(words)) or "help"


async def hybrid_search(session: AsyncSession, question: str, top_k: int | None = None) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = top_k or settings.rag_top_k
    n = settings.rag_candidates

    [vector] = await gateway.embed([question], purpose="query_embed")
    distance = KnowledgeChunk.embedding.cosine_distance(vector).label("distance")
    vector_hits = (await session.execute(select(KnowledgeChunk, distance).order_by(distance).limit(n))).all()
    vector_rows = [row for row, _ in vector_hits]
    similarity = {row.id: 1.0 - float(dist) for row, dist in vector_hits}

    ts_query = func.to_tsquery("simple", _keyword_query(question))
    keyword_rows = (
        (
            await session.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.tsv.op("@@")(ts_query))
                .order_by(func.ts_rank(KnowledgeChunk.tsv, ts_query).desc())
                .limit(n)
            )
        )
        .scalars()
        .all()
    )

    keyword_ids = {row.id for row in keyword_rows}
    fused: dict[int, float] = {}
    by_id: dict[int, KnowledgeChunk] = {}
    for rows in (vector_rows, keyword_rows):
        for rank, row in enumerate(rows):
            fused[row.id] = fused.get(row.id, 0.0) + 1.0 / (RRF_K + rank + 1)
            by_id[row.id] = row

    ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)[: max(top_k * 2, top_k)]
    results = [
        RetrievedChunk(r.id, r.doc_id, r.doc_title, r.section, r.content, score, similarity.get(r.id, 0.0), r.id in keyword_ids)
        for chunk_id, score in ranked
        for r in [by_id[chunk_id]]
    ]
    if settings.rag_rerank == "llm" and results:
        results = await _llm_rerank(question, results)
    return results[:top_k]


async def _llm_rerank(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    passages = "\n".join(f"Passage {i + 1}:\n{c.content}" for i, c in enumerate(chunks))
    result = await gateway.chat(
        [
            {"role": "system", "content": "Score how well each passage answers the question, from 0 to 1."},
            {"role": "user", "content": f"Question: {question}\n{passages}"},
        ],
        purpose="rerank",
        response_model=RerankScores,
    )
    scores = result.parsed.scores if result.parsed else []
    for chunk, score in zip(chunks, scores, strict=False):
        chunk.score = chunk.score + score  # keep fusion order as a tie-breaker
    return sorted(chunks, key=lambda c: c.score, reverse=True)
