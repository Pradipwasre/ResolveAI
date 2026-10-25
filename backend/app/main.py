"""FastAPI application: API, Telegram webhook, WebSocket and the dashboard UI."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import auth, evals, knowledge, metrics, simulator, telegram, tickets, ws
from app.config import get_settings
from app.db import SessionLocal, engine
from app.logging_setup import configure_logging, log
from app.redis_client import close_redis, get_redis
from app.startup import run_startup_tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.run_startup_tasks:
        await run_startup_tasks()
    log.info("api_started", provider=settings.llm_provider, environment=settings.environment)
    yield
    await close_redis()
    await engine.dispose()


settings = get_settings()
app = FastAPI(title="ResolveAI API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, tickets, metrics, knowledge, simulator, telegram, evals, ws):
    app.include_router(module.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready() -> dict:
    """Readiness: database and Redis are reachable."""
    async with SessionLocal() as session:
        await session.execute(text("select 1"))
    await get_redis().ping()
    return {"status": "ready"}


static = Path(settings.static_dir)
if static.is_dir():
    # The dashboard is a static Next.js export served from the same origin as the API.
    app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")
