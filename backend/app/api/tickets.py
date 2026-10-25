"""Ticket inbox for support agents: list, read, reply, take over, resolve, approvals."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.prompts import template
from app.api.auth import current_agent
from app.channels.telegram import CSAT_KEYBOARD, send_to_customer
from app.config import get_settings
from app.db import get_session
from app.models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Customer,
    Message,
    MessageRole,
    Order,
    Ticket,
    TicketStatus,
)
from app.services import events, memory

router = APIRouter(prefix="/api", tags=["tickets"], dependencies=[Depends(current_agent)])


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DecisionBody(BaseModel):
    approve: bool
    note: str = ""


def ticket_summary(t: Ticket, last: Message | None, pending: int) -> dict:
    return {
        "id": t.id,
        "subject": t.subject,
        "status": t.status,
        "priority": t.priority,
        "intent": t.intent,
        "sentiment": t.sentiment,
        "language": t.language,
        "channel": t.channel,
        "customer": {
            "id": t.customer.id,
            "name": t.customer.name,
            "tier": t.customer.tier,
            "verified": t.customer.verified,
            "telegram": t.customer.telegram_username,
        },
        "last_message": {"role": last.role, "content": last.content[:140], "at": last.created_at} if last else None,
        "pending_approvals": pending,
        "escalation_reason": t.escalation_reason,
        "csat": t.csat,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


@router.get("/tickets")
async def list_tickets(
    status: str | None = None, q: str | None = None, limit: int = 100, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    query = select(Ticket).options(selectinload(Ticket.customer)).order_by(Ticket.updated_at.desc()).limit(limit)
    if status == "active":
        query = query.where(Ticket.status != TicketStatus.RESOLVED)
    elif status:
        query = query.where(Ticket.status == status)
    if q:
        like = f"%{q}%"
        query = query.join(Customer).where(or_(Ticket.subject.ilike(like), Customer.name.ilike(like)))
    tickets = (await session.execute(query)).scalars().all()
    ids = [t.id for t in tickets]
    last_ids = select(func.max(Message.id)).where(Message.ticket_id.in_(ids)).group_by(Message.ticket_id)
    lasts = {m.ticket_id: m for m in (await session.execute(select(Message).where(Message.id.in_(last_ids)))).scalars()}
    pending_rows = await session.execute(
        select(Approval.ticket_id, func.count())
        .where(Approval.ticket_id.in_(ids), Approval.status == ApprovalStatus.PENDING)
        .group_by(Approval.ticket_id)
    )
    pending = dict(pending_rows.all())
    return [ticket_summary(t, lasts.get(t.id), pending.get(t.id, 0)) for t in tickets]


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    ticket = await session.get(Ticket, ticket_id, options=[selectinload(Ticket.customer), selectinload(Ticket.messages)])
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    orders = (
        (
            await session.execute(
                select(Order).where(Order.customer_id == ticket.customer_id).order_by(Order.created_at.desc()).limit(5)
            )
        )
        .scalars()
        .all()
        if ticket.customer.verified
        else []
    )
    approvals = (await session.execute(select(Approval).where(Approval.ticket_id == ticket_id))).scalars().all()
    pending = sum(1 for a in approvals if a.status == ApprovalStatus.PENDING)
    data = ticket_summary(ticket, ticket.messages[-1] if ticket.messages else None, pending)
    data["messages"] = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "is_voice": m.is_voice,
            "sources": m.sources,
            "confidence": m.confidence,
            "latency_ms": m.latency_ms,
            "cost_usd": m.cost_usd,
            "at": m.created_at,
        }
        for m in ticket.messages
    ]
    data["customer"].update({"email": ticket.customer.email, "memory": ticket.customer.memory_summary})
    data["orders"] = [
        {"id": o.id, "status": o.status, "total_inr": o.total_inr, "items": [i["name"] for i in o.items], "placed": o.created_at}
        for o in orders
    ]
    data["approvals"] = [
        {
            "id": a.id,
            "order_id": a.order_id,
            "action": a.action,
            "amount_inr": a.amount_inr,
            "reason": a.reason,
            "status": a.status,
            "decided_by": a.decided_by,
        }
        for a in approvals
    ]
    data["total_cost_usd"] = round(sum(m.cost_usd for m in ticket.messages), 6)
    return data


async def _load(session: AsyncSession, ticket_id: int) -> Ticket:
    ticket = await session.get(Ticket, ticket_id, options=[selectinload(Ticket.customer)])
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.post("/tickets/{ticket_id}/reply")
async def reply(
    ticket_id: int, body: ReplyBody, agent: dict = Depends(current_agent), session: AsyncSession = Depends(get_session)
) -> dict:
    ticket = await _load(session, ticket_id)
    if ticket.status == TicketStatus.RESOLVED:
        raise HTTPException(400, "Ticket is resolved")
    session.add(Message(ticket_id=ticket.id, role=MessageRole.AGENT, content=body.text))
    session.add(AuditLog(actor=agent["sub"], action="agent_reply", ticket_id=ticket.id, detail={}))
    ticket.status = TicketStatus.HUMAN
    ticket.updated_at = datetime.now(UTC)
    await session.commit()
    await memory.append(ticket.id, MessageRole.AGENT, body.text)
    delivered = await send_to_customer(ticket.customer, body.text)
    await events.publish("message", ticket_id=ticket.id, role="agent")
    return {"ok": True, "delivered": delivered}


@router.post("/tickets/{ticket_id}/takeover")
async def takeover(ticket_id: int, agent: dict = Depends(current_agent), session: AsyncSession = Depends(get_session)) -> dict:
    ticket = await _load(session, ticket_id)
    ticket.status = TicketStatus.HUMAN
    session.add(AuditLog(actor=agent["sub"], action="takeover", ticket_id=ticket.id, detail={}))
    await session.commit()
    await events.publish("ticket_updated", ticket_id=ticket.id)
    return {"ok": True}


@router.post("/tickets/{ticket_id}/handback")
async def handback(ticket_id: int, agent: dict = Depends(current_agent), session: AsyncSession = Depends(get_session)) -> dict:
    ticket = await _load(session, ticket_id)
    ticket.status = TicketStatus.OPEN
    ticket.escalation_reason = None
    session.add(AuditLog(actor=agent["sub"], action="handback_to_ai", ticket_id=ticket.id, detail={}))
    await session.commit()
    await events.publish("ticket_updated", ticket_id=ticket.id)
    return {"ok": True}


@router.post("/tickets/{ticket_id}/resolve")
async def resolve(ticket_id: int, agent: dict = Depends(current_agent), session: AsyncSession = Depends(get_session)) -> dict:
    ticket = await _load(session, ticket_id)
    human_involved = (
        await session.execute(select(func.count()).where(Message.ticket_id == ticket.id, Message.role == MessageRole.AGENT))
    ).scalar_one() > 0 or ticket.escalation_reason is not None
    ticket.status = TicketStatus.RESOLVED
    ticket.resolved_by = "human" if human_involved else "ai"
    ticket.resolved_at = datetime.now(UTC)
    session.add(AuditLog(actor=agent["sub"], action="resolve", ticket_id=ticket.id, detail={}))
    await session.commit()
    await send_to_customer(
        ticket.customer, template("csat", ticket.language, get_settings().client_name), CSAT_KEYBOARD(ticket.id)
    )
    await events.publish("ticket_resolved", ticket_id=ticket.id, by=ticket.resolved_by)
    return {"ok": True}


@router.get("/approvals")
async def list_approvals(status: str = "pending", session: AsyncSession = Depends(get_session)) -> list[dict]:
    query = select(Approval).options(selectinload(Approval.ticket).selectinload(Ticket.customer)).order_by(Approval.id.desc())
    if status != "all":
        query = query.where(Approval.status == status)
    rows = (await session.execute(query.limit(200))).scalars().all()
    return [
        {
            "id": a.id,
            "ticket_id": a.ticket_id,
            "order_id": a.order_id,
            "action": a.action,
            "amount_inr": a.amount_inr,
            "reason": a.reason,
            "status": a.status,
            "customer": a.ticket.customer.name,
            "decided_by": a.decided_by,
            "created_at": a.created_at,
            "decided_at": a.decided_at,
        }
        for a in rows
    ]


@router.post("/approvals/{approval_id}/decide")
async def decide(
    approval_id: int, body: DecisionBody, agent: dict = Depends(current_agent), session: AsyncSession = Depends(get_session)
) -> dict:
    approval = await session.get(Approval, approval_id, options=[selectinload(Approval.ticket).selectinload(Ticket.customer)])
    if approval is None:
        raise HTTPException(404, "Approval not found")
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(400, "Already decided")
    order = await session.get(Order, approval.order_id)
    approval.status = ApprovalStatus.APPROVED if body.approve else ApprovalStatus.REJECTED
    approval.decided_by = agent["sub"]
    approval.decided_at = datetime.now(UTC)
    if order:
        order.status = "refunded" if body.approve else "delivered" if order.delivered_at else "shipped"
    session.add(
        AuditLog(
            actor=agent["sub"],
            action=f"refund_{approval.status}",
            ticket_id=approval.ticket_id,
            detail={"order_id": approval.order_id, "amount": approval.amount_inr, "note": body.note},
        )
    )
    if body.approve:
        text = (
            f"Good news: your refund of Rs {approval.amount_inr:,.0f} for order {approval.order_id} has been approved. "
            "It will reach your original payment method within the refund timeline for that method."
        )
    else:
        text = f"Update on order {approval.order_id}: we could not approve the refund request." + (
            f" Reason: {body.note}" if body.note else " A team member will explain the next steps here."
        )
    session.add(Message(ticket_id=approval.ticket_id, role=MessageRole.AGENT, content=text))
    await session.commit()
    await send_to_customer(approval.ticket.customer, text)
    await events.publish("approval_decided", ticket_id=approval.ticket_id, approval_id=approval.id)
    return {"ok": True, "status": approval.status}
