"""Test console: chat with the agent as a customer, without Telegram."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_agent
from app.db import get_session
from app.models import Customer
from app.services.conversation import handle_customer_message

router = APIRouter(prefix="/api/simulator", tags=["simulator"], dependencies=[Depends(current_agent)])


class SimMessage(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    customer_id: int | None = None


@router.post("/message")
async def message(body: SimMessage, session: AsyncSession = Depends(get_session)) -> dict:
    customer = await session.get(Customer, body.customer_id) if body.customer_id else None
    if customer is None:
        customer = Customer(name="Web test customer", language="en")
        session.add(customer)
        await session.commit()
    result = await handle_customer_message(customer.id, body.text, channel="web")
    return {
        "customer_id": result.customer_id,
        "ticket_id": result.ticket_id,
        "reply": result.reply,
        "route": result.route,
        "escalated": result.escalated,
        "sources": result.sources,
        "confidence": result.confidence,
        "latency_ms": result.latency_ms,
        "cost_usd": round(result.cost_usd, 6),
    }
