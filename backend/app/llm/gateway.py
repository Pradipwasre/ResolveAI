"""The single entry point for every model call.

It picks the provider, measures latency, computes cost, writes an
llm_calls row and updates the budget counters. Business code never
calls a provider directly, so nothing escapes cost tracking.
"""

import time
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.db import SessionLocal
from app.llm.base import ChatResult, LLMProvider, ModerationResult, Tier, Usage
from app.llm.fake_provider import FakeProvider
from app.llm.pricing import audio_cost, token_cost
from app.logging_setup import log
from app.models import LLMCall
from app.services.costs import add_spend

current_ticket_id: ContextVar[int | None] = ContextVar("current_ticket_id", default=None)
current_customer_id: ContextVar[int | None] = ContextVar("current_customer_id", default=None)


class CostMeter:
    """Mutable running total for one request (LangGraph nodes run in copied contexts,
    so a plain float in a ContextVar would not see their updates)."""

    def __init__(self) -> None:
        self.usd = 0.0


request_cost: ContextVar[CostMeter | None] = ContextVar("request_cost", default=None)


def start_cost_meter() -> CostMeter:
    meter = CostMeter()
    request_cost.set(meter)
    return meter


@lru_cache
def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is empty")
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    return FakeProvider(settings.embedding_dim)


async def _record(purpose: str, model: str, usage: Usage, latency_ms: int, success: bool = True) -> float:
    if usage.audio_seconds:
        cost = audio_cost(model, usage.audio_seconds)
    else:
        cost = token_cost(model, usage.input_tokens, usage.output_tokens, usage.cached_tokens)
    if (meter := request_cost.get()) is not None:
        meter.usd += cost
    customer_id = current_customer_id.get()
    try:
        async with SessionLocal() as session:
            session.add(
                LLMCall(
                    ticket_id=current_ticket_id.get(),
                    customer_id=customer_id,
                    purpose=purpose,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_tokens=usage.cached_tokens,
                    audio_seconds=usage.audio_seconds,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    success=success,
                )
            )
            await session.commit()
        await add_spend(cost, customer_id)
    except Exception as exc:  # cost tracking must never break a customer reply
        log.warning("cost_record_failed", error=str(exc))
    log.info(
        "llm_call",
        purpose=purpose,
        model=model,
        latency_ms=latency_ms,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=round(cost, 6),
    )
    return cost


async def chat(
    messages: list[dict[str, Any]],
    *,
    purpose: str,
    tier: Tier = "fast",
    response_model: type[BaseModel] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> ChatResult:
    provider = get_provider()
    start = time.perf_counter()
    try:
        result = await provider.chat(messages, tier=tier, purpose=purpose, response_model=response_model, tools=tools)
    except Exception:
        await _record(purpose, provider.model_for(tier), Usage(), int((time.perf_counter() - start) * 1000), success=False)
        raise
    await _record(purpose, result.model, result.usage, int((time.perf_counter() - start) * 1000))
    return result


async def embed(texts: list[str], purpose: str = "embed") -> list[list[float]]:
    provider = get_provider()
    start = time.perf_counter()
    vectors, usage, model = await provider.embed(texts)
    await _record(purpose, model, usage, int((time.perf_counter() - start) * 1000))
    return vectors


async def transcribe(audio: bytes, filename: str, duration_seconds: float) -> str:
    provider = get_provider()
    start = time.perf_counter()
    text, usage, model = await provider.transcribe(audio, filename)
    usage.audio_seconds = duration_seconds
    await _record("transcribe", model, usage, int((time.perf_counter() - start) * 1000))
    return text


async def speak(text: str) -> bytes:
    provider = get_provider()
    start = time.perf_counter()
    audio, usage, model = await provider.speak(text)
    await _record("tts", model, usage, int((time.perf_counter() - start) * 1000))
    return audio


async def moderate(text: str) -> ModerationResult:
    # The moderation endpoint is free, so it is not recorded as spend.
    if not get_settings().use_moderation_api:
        return ModerationResult(flagged=False)
    try:
        return await get_provider().moderate(text)
    except Exception as exc:
        log.warning("moderation_failed", error=str(exc))
        return ModerationResult(flagged=False)
