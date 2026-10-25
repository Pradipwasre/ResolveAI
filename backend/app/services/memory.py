"""Conversation memory.

Short-term: the last N turns of a ticket live in Redis with a TTL (fast,
self-cleaning). Long-term: when the window overflows, older turns are
summarised by a small model into the customer's memory_summary in
Postgres. Before each model call the context is trimmed to a token
budget, so prompt size and cost stay flat however long a chat runs.
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import gateway
from app.llm.fake_provider import estimate_tokens
from app.models import Customer, Message
from app.redis_client import get_redis

ROLE_MAP = {"customer": "user", "ai": "assistant", "agent": "assistant"}


def _key(ticket_id: int) -> str:
    return f"memory:ticket:{ticket_id}"


async def load_history(session: AsyncSession, ticket_id: int) -> list[dict[str, str]]:
    settings = get_settings()
    redis = get_redis()
    raw = await redis.lrange(_key(ticket_id), 0, -1)
    if raw:
        return [json.loads(item) for item in raw]
    # Cache miss (expired or new process): rebuild from Postgres.
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.ticket_id == ticket_id, Message.role != "system")
                .order_by(Message.id.desc())
                .limit(settings.memory_window_messages)
            )
        )
        .scalars()
        .all()
    )
    history = [{"role": ROLE_MAP.get(m.role, "user"), "content": m.content} for m in reversed(rows)]
    if history:
        await redis.rpush(_key(ticket_id), *[json.dumps(h) for h in history])
        await redis.expire(_key(ticket_id), settings.memory_ttl_seconds)
    return history


async def append(ticket_id: int, role: str, content: str) -> None:
    redis = get_redis()
    await redis.rpush(_key(ticket_id), json.dumps({"role": ROLE_MAP.get(role, "user"), "content": content}))
    await redis.expire(_key(ticket_id), get_settings().memory_ttl_seconds)


async def compact(session: AsyncSession, ticket_id: int, customer: Customer) -> bool:
    """Summarise the oldest turns into long-term memory when the window is full."""
    settings = get_settings()
    redis = get_redis()
    length = await redis.llen(_key(ticket_id))
    if length <= settings.memory_window_messages:
        return False
    overflow = length - settings.memory_window_messages // 2
    old = [json.loads(item) for item in await redis.lrange(_key(ticket_id), 0, overflow - 1)]
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in old)
    result = await gateway.chat(
        [
            {
                "role": "system",
                "content": "Update the customer's support memory. Keep facts that matter for future help: "
                "orders discussed, problems, promises made, preferences. Max 80 words. No personal data.",
            },
            {"role": "user", "content": f"Current memory: {customer.memory_summary or 'none'}\n\nNew turns:\n{transcript}"},
        ],
        purpose="summarize",
    )
    customer.memory_summary = result.text.strip()[:1000]
    await session.commit()
    await redis.ltrim(_key(ticket_id), overflow, -1)
    return True


def trim_to_budget(history: list[dict[str, str]], max_tokens: int) -> list[dict[str, str]]:
    """Drop the oldest turns until the history fits the token budget."""
    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(history):
        cost = estimate_tokens(message["content"]) + 4
        if used + cost > max_tokens:
            break
        kept.append(message)
        used += cost
    return list(reversed(kept))
