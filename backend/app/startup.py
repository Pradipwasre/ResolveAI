"""One-time startup work: seed demo data and index the knowledge base.

A Redis lock makes sure only one container does it when several start together.
"""

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import log
from app.models import KnowledgeChunk
from app.rag.ingest import ingest_directory
from app.redis_client import get_redis
from app.seed import seed_if_empty


async def run_startup_tasks() -> None:
    settings = get_settings()
    redis = get_redis()
    async with redis.lock("startup-lock", timeout=300, blocking_timeout=300):
        await seed_if_empty()
        async with SessionLocal() as session:
            count = (await session.execute(select(func.count(KnowledgeChunk.id)))).scalar_one()
            if count == 0:
                await ingest_directory(session, settings.knowledge_dir)
    log.info("startup_complete")
