"""Cost management: per-call recording, daily budgets and spend counters.

Every model call is written to the llm_calls table (for reporting) and
added to Redis counters (for fast budget checks before each call).
"""

from datetime import UTC, datetime

from app.config import get_settings
from app.redis_client import get_redis

DAY_TTL = 60 * 60 * 48


def _day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def add_spend(cost_usd: float, customer_id: int | None) -> None:
    redis = get_redis()
    day = _day()
    pipe = redis.pipeline()
    pipe.incrbyfloat(f"spend:{day}:total", cost_usd)
    pipe.expire(f"spend:{day}:total", DAY_TTL)
    if customer_id is not None:
        pipe.incrbyfloat(f"spend:{day}:customer:{customer_id}", cost_usd)
        pipe.expire(f"spend:{day}:customer:{customer_id}", DAY_TTL)
    await pipe.execute()


async def spend_today(customer_id: int | None = None) -> float:
    key = f"spend:{_day()}:customer:{customer_id}" if customer_id else f"spend:{_day()}:total"
    value = await get_redis().get(key)
    return float(value or 0)


async def budget_status(customer_id: int | None) -> str | None:
    """Return a reason string if a budget is exhausted, else None."""
    settings = get_settings()
    if await spend_today() >= settings.daily_budget_usd:
        return "daily_budget"
    if customer_id and await spend_today(customer_id) >= settings.per_customer_daily_budget_usd:
        return "customer_budget"
    return None
