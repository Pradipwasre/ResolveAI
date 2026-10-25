"""Tools the agent can call. Each one is scoped to the verified customer,
so the model can never read or change another customer's orders."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Approval, AuditLog, Order

RETURN_WINDOW_DAYS = {"electronics": 7, "fashion": 15, "default": 10}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_orders",
            "description": "List the customer's 5 most recent orders with status.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Get status, items, courier, tracking number and dates for one order.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "Order id like UC-10001"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel an order that has not shipped yet.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_return",
            "description": "Schedule a free return pickup for a delivered order within its return window.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["order_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_refund",
            "description": "Raise a refund request for an order. A human must approve it before money moves.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["order_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
]


def _normalise(order_id: str) -> str:
    digits = "".join(ch for ch in order_id if ch.isdigit())
    return f"UC-{digits}"


def _order_view(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.id,
        "status": order.status,
        "items": [i["name"] for i in order.items],
        "total_inr": order.total_inr,
        "payment": order.payment_method,
        "courier": order.courier,
        "tracking_number": order.tracking_number,
        "placed_on": order.created_at.date().isoformat() if order.created_at else None,
        "expected_delivery": order.expected_delivery.date().isoformat() if order.expected_delivery else None,
        "delivered_on": order.delivered_at.date().isoformat() if order.delivered_at else None,
    }


class CustomerTools:
    def __init__(self, session: AsyncSession, customer_id: int, ticket_id: int):
        self.session = session
        self.customer_id = customer_id
        self.ticket_id = ticket_id
        self.approvals_created: list[int] = []
        self.actions: list[str] = []

    async def _own_order(self, order_id: str) -> Order | None:
        order = await self.session.get(Order, _normalise(order_id))
        if order is None or order.customer_id != self.customer_id:
            return None
        return order

    async def _audit(self, action: str, **detail: Any) -> None:
        self.session.add(AuditLog(actor="ai", action=action, ticket_id=self.ticket_id, detail=detail))
        self.actions.append(action)

    async def run(self, name: str, arguments: dict[str, Any]) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            result = await handler(**arguments)
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        await self.session.commit()
        return json.dumps(result, default=str)

    async def tool_list_orders(self) -> dict[str, Any]:
        rows = (
            (
                await self.session.execute(
                    select(Order).where(Order.customer_id == self.customer_id).order_by(Order.created_at.desc()).limit(5)
                )
            )
            .scalars()
            .all()
        )
        return {"orders": [_order_view(o) for o in rows]}

    async def tool_get_order(self, order_id: str) -> dict[str, Any]:
        order = await self._own_order(order_id)
        if order is None:
            return {"error": "No order with that id on this account."}
        return _order_view(order)

    async def tool_cancel_order(self, order_id: str) -> dict[str, Any]:
        order = await self._own_order(order_id)
        if order is None:
            return {"error": "No order with that id on this account."}
        if order.status not in {"placed", "packed"}:
            return {"cancelled": False, "reason": f"Order is already {order.status}; it can no longer be cancelled."}
        order.status = "cancelled"
        await self._audit("cancel_order", order_id=order.id)
        return {"cancelled": True, "order_id": order.id, "refund": "Refund starts automatically if prepaid."}

    async def tool_request_return(self, order_id: str, reason: str) -> dict[str, Any]:
        order = await self._own_order(order_id)
        if order is None:
            return {"error": "No order with that id on this account."}
        if order.status != "delivered" or order.delivered_at is None:
            return {"scheduled": False, "reason": f"Only delivered orders can be returned; this order is {order.status}."}
        category = order.items[0].get("category", "default") if order.items else "default"
        window = RETURN_WINDOW_DAYS.get(category, RETURN_WINDOW_DAYS["default"])
        if datetime.now(UTC) - order.delivered_at > timedelta(days=window):
            return {"scheduled": False, "reason": f"The {window} day return window for this item has ended."}
        order.status = "return_requested"
        pickup = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
        await self._audit("request_return", order_id=order.id, reason=reason[:200])
        return {
            "scheduled": True,
            "order_id": order.id,
            "pickup_by": pickup,
            "refund_after": "quality check, 1 to 7 business days depending on payment method",
        }

    async def tool_request_refund(self, order_id: str, reason: str) -> dict[str, Any]:
        order = await self._own_order(order_id)
        if order is None:
            return {"error": "No order with that id on this account."}
        if order.status in {"refunded", "refund_pending"}:
            return {"created": False, "reason": f"A refund for this order is already {order.status.replace('_', ' ')}."}
        if order.status in {"placed", "packed"}:
            return {
                "created": False,
                "reason": "This order has not shipped. Cancel it instead and the refund starts automatically.",
            }
        limit = get_settings().refund_auto_approve_limit_inr
        if 0 < limit and order.total_inr <= limit:
            order.status = "refunded"
            await self._audit("auto_refund", order_id=order.id, amount=order.total_inr)
            return {"created": True, "approved": True, "amount_inr": order.total_inr}
        approval = Approval(
            ticket_id=self.ticket_id, order_id=order.id, action="refund", amount_inr=order.total_inr, reason=reason[:500]
        )
        self.session.add(approval)
        order.status = "refund_pending"
        await self.session.flush()
        self.approvals_created.append(approval.id)
        await self._audit("request_refund", order_id=order.id, amount=order.total_inr, approval_id=approval.id)
        return {
            "created": True,
            "approved": False,
            "amount_inr": order.total_inr,
            "next_step": "A team member reviews refund requests, usually within a few hours.",
        }
