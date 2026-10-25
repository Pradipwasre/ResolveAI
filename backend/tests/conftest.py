"""Test setup: a separate Postgres database and Redis DB, the offline LLM, no Telegram token."""

import os

os.environ.setdefault("TEST_DATABASE_URL", "postgresql+asyncpg://resolveai:resolveai@localhost:5432/resolveai_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["LLM_PROVIDER"] = "fake"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["RUN_STARTUP_TASKS"] = "false"
os.environ["STATIC_DIR"] = "/nonexistent"

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.redis_client import get_redis  # noqa: E402

TABLES = "customers, orders, tickets, messages, approvals, llm_calls, guardrail_events, audit_log, agents, eval_runs"


async def _ensure_database() -> None:
    url = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    base, name = url.rsplit("/", 1)
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        if not await conn.fetchval("select 1 from pg_database where datname = $1", name):
            await conn.execute(f'create database "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
async def database():
    await _ensure_database()
    import asyncio

    cfg = Config("alembic.ini")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    from app.startup import run_startup_tasks

    await get_redis().flushdb()
    async with SessionLocal() as session:
        await session.execute(text(f"truncate {TABLES}, knowledge_chunks restart identity cascade"))
        await session.commit()
    await run_startup_tasks()  # seeds data and indexes the knowledge base once
    yield


@pytest.fixture(autouse=True)
async def clean_state(database):
    """Fresh tickets, customers and counters for every test (the knowledge index is kept)."""
    redis = get_redis()
    await redis.flushdb()
    async with SessionLocal() as session:
        await session.execute(text(f"truncate {TABLES} restart identity cascade"))
        await session.commit()
    from app.seed import seed_if_empty

    await seed_if_empty()
    from app.channels.telegram import get_telegram

    get_telegram().sent.clear()
    yield


@pytest.fixture
async def client():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client):
    from app.config import get_settings

    s = get_settings()
    response = await client.post("/api/auth/login", json={"email": s.admin_email, "password": s.admin_password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
async def guest():
    """A new, unverified Telegram customer."""
    from app.models import Customer

    async with SessionLocal() as session:
        customer = Customer(name="Test Guest", telegram_user_id=555, telegram_chat_id=555)
        session.add(customer)
        await session.commit()
        return customer.id
