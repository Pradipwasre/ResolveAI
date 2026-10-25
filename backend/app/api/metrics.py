"""Operational, quality and cost metrics for the dashboard, computed with SQL."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_agent
from app.config import get_settings
from app.db import get_session
from app.models import EvalRun, GuardrailEvent, LLMCall, Message, MessageRole, Ticket, TicketStatus
from app.redis_client import get_redis
from app.services.costs import spend_today

router = APIRouter(prefix="/api/metrics", tags=["metrics"], dependencies=[Depends(current_agent)])


@router.get("/overview")
async def overview(days: int = 7, session: AsyncSession = Depends(get_session)) -> dict:
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=days)
    scalar = lambda q: session.execute(q)  # noqa: E731

    tickets = (await scalar(select(func.count(Ticket.id)).where(Ticket.created_at >= since))).scalar_one()
    by_status = dict(
        (await scalar(select(Ticket.status, func.count()).where(Ticket.created_at >= since).group_by(Ticket.status))).all()
    )
    resolved_ai = (await scalar(select(func.count()).where(Ticket.created_at >= since, Ticket.resolved_by == "ai"))).scalar_one()
    resolved = by_status.get(TicketStatus.RESOLVED, 0)
    escalated = (
        await scalar(select(func.count()).where(Ticket.created_at >= since, Ticket.escalation_reason.is_not(None)))
    ).scalar_one()
    intents = dict(
        (
            await scalar(
                select(Ticket.intent, func.count())
                .where(Ticket.created_at >= since, Ticket.intent.is_not(None))
                .group_by(Ticket.intent)
            )
        ).all()
    )

    latency = (
        await scalar(
            select(
                func.avg(Message.latency_ms),
                func.percentile_cont(0.5).within_group(Message.latency_ms),
                func.percentile_cont(0.95).within_group(Message.latency_ms),
            ).where(Message.created_at >= since, Message.role == MessageRole.AI, Message.latency_ms.is_not(None))
        )
    ).one()

    cost = (
        await scalar(
            select(
                func.coalesce(func.sum(LLMCall.cost_usd), 0.0),
                func.coalesce(func.sum(LLMCall.input_tokens), 0),
                func.coalesce(func.sum(LLMCall.output_tokens), 0),
                func.coalesce(func.sum(LLMCall.cached_tokens), 0),
                func.count(LLMCall.id),
                func.count(LLMCall.id).filter(LLMCall.success.is_(False)),
            ).where(LLMCall.created_at >= since)
        )
    ).one()
    by_model = [
        {"model": m, "calls": n, "cost_usd": round(c, 6), "avg_latency_ms": int(lat or 0)}
        for m, n, c, lat in (
            await scalar(
                select(LLMCall.model, func.count(), func.sum(LLMCall.cost_usd), func.avg(LLMCall.latency_ms))
                .where(LLMCall.created_at >= since)
                .group_by(LLMCall.model)
                .order_by(func.sum(LLMCall.cost_usd).desc())
            )
        ).all()
    ]
    by_purpose = [
        {"purpose": p, "calls": n, "cost_usd": round(c, 6)}
        for p, n, c in (
            await scalar(
                select(LLMCall.purpose, func.count(), func.sum(LLMCall.cost_usd))
                .where(LLMCall.created_at >= since)
                .group_by(LLMCall.purpose)
                .order_by(func.sum(LLMCall.cost_usd).desc())
            )
        ).all()
    ]

    day = func.date_trunc("day", Ticket.created_at)
    ticket_series = dict((await scalar(select(day, func.count()).where(Ticket.created_at >= since).group_by(day))).all())
    esc_series = dict(
        (
            await scalar(
                select(day, func.count()).where(Ticket.created_at >= since, Ticket.escalation_reason.is_not(None)).group_by(day)
            )
        ).all()
    )
    cday = func.date_trunc("day", LLMCall.created_at)
    cost_series = dict(
        (await scalar(select(cday, func.sum(LLMCall.cost_usd)).where(LLMCall.created_at >= since).group_by(cday))).all()
    )
    series = []
    for i in range(days - 1, -1, -1):
        d = (datetime.now(UTC) - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        key = next((k for k in ticket_series | cost_series if k.date() == d.date()), None)
        series.append(
            {
                "date": d.date().isoformat(),
                "tickets": ticket_series.get(key, 0) if key else 0,
                "escalations": esc_series.get(key, 0) if key else 0,
                "cost_usd": round(cost_series.get(key, 0.0) or 0.0, 6) if key else 0.0,
            }
        )

    guardrails = dict(
        (
            await scalar(
                select(GuardrailEvent.kind, func.count()).where(GuardrailEvent.created_at >= since).group_by(GuardrailEvent.kind)
            )
        ).all()
    )
    csat = (
        await scalar(
            select(func.avg(Ticket.csat), func.count(Ticket.csat)).where(Ticket.created_at >= since, Ticket.csat.is_not(None))
        )
    ).one()

    redis = get_redis()
    hits = misses = 0
    for i in range(days):
        d = (datetime.now(UTC) - timedelta(days=i)).strftime("%Y-%m-%d")
        hits += int(await redis.get(f"cache:{d}:hits") or 0)
        misses += int(await redis.get(f"cache:{d}:misses") or 0)

    latest_eval = (await scalar(select(EvalRun).order_by(EvalRun.id.desc()).limit(1))).scalar_one_or_none()
    total_cost = float(cost[0])
    return {
        "window_days": days,
        "tickets": {
            "total": tickets,
            "by_status": by_status,
            "escalated": escalated,
            "resolved": resolved,
            "resolved_by_ai": resolved_ai,
            "intents": intents,
        },
        "rates": {
            "ai_resolution_rate": round(resolved_ai / resolved, 3) if resolved else None,
            "escalation_rate": round(escalated / tickets, 3) if tickets else None,
            "cache_hit_rate": round(hits / (hits + misses), 3) if hits + misses else None,
            "llm_error_rate": round(float(cost[5]) / cost[4], 4) if cost[4] else None,
        },
        "latency_ms": {"avg": int(latency[0] or 0), "p50": int(latency[1] or 0), "p95": int(latency[2] or 0)},
        "cost": {
            "total_usd": round(total_cost, 6),
            "per_ticket_usd": round(total_cost / tickets, 6) if tickets else 0.0,
            "input_tokens": int(cost[1]),
            "output_tokens": int(cost[2]),
            "cached_tokens": int(cost[3]),
            "llm_calls": int(cost[4]),
            "today_usd": round(await spend_today(), 6),
            "daily_budget_usd": settings.daily_budget_usd,
            "by_model": by_model,
            "by_purpose": by_purpose,
        },
        "csat": {"average": round(float(csat[0]), 2) if csat[0] else None, "responses": csat[1]},
        "guardrails": guardrails,
        "series": series,
        "latest_eval": {
            "at": latest_eval.created_at,
            "provider": latest_eval.provider,
            "passed": latest_eval.passed,
            "metrics": latest_eval.metrics,
        }
        if latest_eval
        else None,
        "provider": settings.llm_provider,
        "models": {"fast": settings.llm_model_fast, "smart": settings.llm_model_smart},
    }
