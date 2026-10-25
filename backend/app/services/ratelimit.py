"""Per-customer rate limiting (fixed window in Redis)."""

from app.config import get_settings
from app.redis_client import get_redis


async def allow_message(customer_key: str) -> bool:
    settings = get_settings()
    redis = get_redis()
    key = f"ratelimit:{customer_key}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.rate_limit_window_seconds)
    return count <= settings.rate_limit_messages
