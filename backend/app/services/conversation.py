"""Handles one customer message end to end, whatever channel it came from.

    rate limit -> PII masking -> store -> (human owns ticket? stop)
    -> injection and moderation checks -> budget check -> agent -> store reply

Channels (Telegram, the web test console) only translate their own
format into `handle_customer_message` and send back the reply.
"""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import AgentContext, support_agent
from app.agent.prompts import template
from app.config import get_settings
from app.db import SessionLocal
from app.guardrails.injection import detect_injection
from app.guardrails.pii import mask_pii
from app.llm import gateway
from app.llm.fake_provider import detect_language
from app.logging_setup import log
from app.models import Customer, GuardrailEvent, Message, MessageRole, Ticket, TicketStatus
from app.services import events, memory
from app.services.costs import budget_status
from app.services.ratelimit import allow_message


@dataclass
class Outgoing:
    ticket_id: int
    customer_id: int
    reply: str | None  # None when a human owns the ticket
    language: str = "en"
    escalated: bool = False
    sources: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0
    route: str = ""


async def get_or_open_ticket(session: AsyncSession, customer: Customer, channel: str) -> Ticket:
    ticket = (
        await session.execute(
            select(Ticket)
            .where(Ticket.customer_id == customer.id, Ticket.status != TicketStatus.RESOLVED)
            .order_by(Ticket.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ticket is None:
        ticket = Ticket(customer_id=customer.id, channel=channel, status=TicketStatus.OPEN)
        session.add(ticket)
        await session.commit()
        await events.publish("ticket_created", ticket_id=ticket.id)
    return ticket


async def _save(session: AsyncSession, ticket: Ticket, role: str, content: str, **extra: Any) -> Message:
    message = Message(ticket_id=ticket.id, role=role, content=content, **extra)
    session.add(message)
    ticket.updated_at = datetime.now(UTC)
    await session.commit()
    await events.publish("message", ticket_id=ticket.id, role=role)
    return message


async def _guard(session: AsyncSession, ticket_id: int, kind: str, action: str, detail: str = "") -> None:
    session.add(GuardrailEvent(ticket_id=ticket_id, stage="input", kind=kind, action=action, detail=detail[:500]))
    await session.commit()


async def _escalate(session: AsyncSession, ticket: Ticket, reason: str, high: bool = False) -> None:
    ticket.status = TicketStatus.NEEDS_HUMAN
    ticket.escalation_reason = reason[:255]
    if high:
        ticket.priority = "high"
    await session.commit()
    await events.publish("ticket_escalated", ticket_id=ticket.id, reason=reason)


async def handle_customer_message(customer_id: int, text: str, channel: str, is_voice: bool = False) -> Outgoing:
    settings = get_settings()
    started = time.perf_counter()
    meter = gateway.start_cost_meter()
    gateway.current_customer_id.set(customer_id)

    async with SessionLocal() as session:
        customer = await session.get(Customer, customer_id)
        if customer is None:
            raise ValueError(f"unknown customer {customer_id}")
        ticket = await get_or_open_ticket(session, customer, channel)
        gateway.current_ticket_id.set(ticket.id)
        language = detect_language(text)
        out = Outgoing(ticket_id=ticket.id, customer_id=customer.id, reply=None, language=language)

        # 1. Rate limit (before anything costs money)
        if not await allow_message(f"customer:{customer.id}"):
            await _guard(session, ticket.id, "rate_limit", "blocked")
            out.reply = template("rate_limited", language, settings.client_name)
            return out

        # 2. Mask personal data; keep emails aside for verification only
        pii = mask_pii(text)
        if pii.found:
            await _guard(session, ticket.id, "pii", "masked", ",".join(pii.found))
        history = await memory.load_history(session, ticket.id)
        await _save(session, ticket, MessageRole.CUSTOMER, pii.masked_text, is_voice=is_voice)
        await memory.append(ticket.id, MessageRole.CUSTOMER, pii.masked_text)

        # 3. A human owns this ticket: the AI stays quiet
        if ticket.status in {TicketStatus.HUMAN, TicketStatus.NEEDS_HUMAN}:
            return out

        # 4. Prompt injection and moderation
        injection = detect_injection(text)
        moderation = await gateway.moderate(pii.masked_text) if settings.llm_provider == "openai" else None
        if injection or (moderation and moderation.flagged):
            kind = "injection" if injection else "moderation"
            detail = injection or ",".join(moderation.categories if moderation else [])
            await _guard(session, ticket.id, kind, "blocked", detail)
            out.reply = template("blocked", language, settings.client_name)
            await _save(session, ticket, MessageRole.AI, out.reply)
            await memory.append(ticket.id, MessageRole.AI, out.reply)
            return out

        # 5. Cost budget
        if reason := await budget_status(customer.id):
            await _guard(session, ticket.id, "budget", "escalated", reason)
            await _escalate(session, ticket, reason)
            out.reply, out.escalated = template("budget", language, settings.client_name), True
            await _save(session, ticket, MessageRole.AI, out.reply)
            return out

        # 6. The agent
        state_in = {
            "ticket_id": ticket.id,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "customer_tier": customer.tier,
            "verified": customer.verified,
            "memory": customer.memory_summary,
            "text": pii.masked_text,
            "emails": [e.lower() for e in pii.emails],
            "history": history,
            "language": language,
        }
        try:
            state = await support_agent.ainvoke(state_in, context=AgentContext(session=session))
        except Exception as exc:
            log.exception("agent_failed", ticket_id=ticket.id, error=str(exc))
            await session.rollback()
            await _escalate(session, ticket, f"agent_error: {type(exc).__name__}")
            out.reply, out.escalated = template("error", language, settings.client_name), True
            await _save(session, ticket, MessageRole.AI, out.reply)
            return out

        # 7. Apply results to the ticket
        await session.refresh(ticket)
        c = state.get("classification", {})
        out.language = state.get("language", language)
        ticket.intent = c.get("intent", ticket.intent)
        ticket.sentiment = c.get("sentiment", ticket.sentiment)
        ticket.language = out.language
        if ticket.subject == "New conversation" and c.get("subject") and c.get("intent") != "greeting":
            ticket.subject = c["subject"][:200]
        out.customer_id = state.get("customer_id", customer.id)
        if state.get("escalate_reason"):
            out.escalated = True
            high = c.get("sentiment") == "angry" or customer.tier == "plus"
            await _escalate(session, ticket, state["escalate_reason"], high=high)
        if state.get("approvals_created"):
            await events.publish("approval_created", ticket_id=ticket.id)

        out.reply = state.get("reply") or template("error", out.language, settings.client_name)
        out.sources = state.get("sources", [])
        out.confidence = state.get("confidence")
        out.route = state.get("reply_kind", "")
        out.latency_ms = int((time.perf_counter() - started) * 1000)
        out.cost_usd = meter.usd
        await _save(
            session,
            ticket,
            MessageRole.AI,
            out.reply,
            sources=out.sources or None,
            confidence=out.confidence,
            latency_ms=out.latency_ms,
            cost_usd=out.cost_usd,
        )
        await memory.append(ticket.id, MessageRole.AI, out.reply)

        merged = await session.get(Customer, out.customer_id)
        if merged is not None:
            await memory.compact(session, ticket.id, merged)
        log.info(
            "customer_message_handled",
            ticket_id=ticket.id,
            route=out.route,
            escalated=out.escalated,
            latency_ms=out.latency_ms,
            cost_usd=round(out.cost_usd, 6),
        )
        return out
