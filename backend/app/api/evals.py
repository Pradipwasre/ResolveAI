"""Run the evaluation suite from the dashboard and read results."""

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_agent
from app.db import get_session
from app.evals import run_all
from app.logging_setup import log
from app.models import EvalRun
from app.redis_client import get_redis

router = APIRouter(prefix="/api/evals", tags=["evals"], dependencies=[Depends(current_agent)])


async def _run() -> None:
    redis = get_redis()
    try:
        await run_all()
    except Exception as exc:
        log.exception("eval_run_failed", error=str(exc))
    finally:
        await redis.delete("evals:running")


@router.get("")
async def list_runs(session: AsyncSession = Depends(get_session)) -> dict:
    runs = (await session.execute(select(EvalRun).order_by(EvalRun.id.desc()).limit(20))).scalars().all()
    return {
        "running": bool(await get_redis().get("evals:running")),
        "runs": [
            {"id": r.id, "at": r.created_at, "provider": r.provider, "passed": r.passed, "metrics": r.metrics} for r in runs
        ],
    }


@router.post("/run")
async def run(background: BackgroundTasks) -> dict:
    if not await get_redis().set("evals:running", 1, nx=True, ex=1800):
        return {"started": False, "reason": "An evaluation is already running"}
    background.add_task(_run)
    return {"started": True}
