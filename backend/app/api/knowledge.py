"""Knowledge base management and a retrieval debugger."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_agent
from app.config import get_settings
from app.db import get_session
from app.models import KnowledgeChunk
from app.rag.ingest import ingest_directory
from app.rag.retriever import hybrid_search

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(current_agent)])


@router.get("")
async def list_documents(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await session.execute(
        select(KnowledgeChunk.doc_id, KnowledgeChunk.doc_title, func.count(), func.max(KnowledgeChunk.created_at))
        .group_by(KnowledgeChunk.doc_id, KnowledgeChunk.doc_title)
        .order_by(KnowledgeChunk.doc_title)
    )
    return [{"doc_id": d, "title": t, "chunks": n, "indexed_at": at} for d, t, n, at in rows.all()]


@router.post("/reindex")
async def reindex(session: AsyncSession = Depends(get_session)) -> dict:
    return await ingest_directory(session, get_settings().knowledge_dir)


@router.get("/search")
async def search(q: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    chunks = await hybrid_search(session, q, top_k=5)
    return [
        {
            "doc": c.doc_title,
            "section": c.section,
            "content": c.content,
            "fused_score": round(c.score, 4),
            "similarity": round(c.similarity, 3),
            "keyword_hit": c.keyword_hit,
        }
        for c in chunks
    ]
