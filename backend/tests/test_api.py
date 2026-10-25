from sqlalchemy import select

from app.channels.telegram import get_telegram, process_update
from app.db import SessionLocal
from app.models import Approval, Customer, Ticket
from app.services.conversation import handle_customer_message


def tg_update(update_id: int, text: str | None = None, voice: bool = False) -> dict:
    message = {
        "message_id": update_id,
        "chat": {"id": 777, "type": "private"},
        "from": {"id": 777, "first_name": "Meera", "username": "meera_k"},
    }
    if voice:
        message["voice"] = {"file_id": "abc", "duration": 4}
    else:
        message["text"] = text
    return {"update_id": update_id, "message": message}


async def test_login_rejects_wrong_password(client):
    response = await client.post("/api/auth/login", json={"email": "admin@resolveai.dev", "password": "nope"})
    assert response.status_code == 401


async def test_api_requires_auth(client):
    assert (await client.get("/api/tickets")).status_code == 401


async def test_webhook_rejects_bad_secret(client):
    response = await client.post(
        "/api/telegram/webhook", json=tg_update(1, "hi"), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}
    )
    assert response.status_code == 403


async def test_webhook_queues_update_once(client):
    from app.config import get_settings
    from app.redis_client import get_redis

    headers = {"X-Telegram-Bot-Api-Secret-Token": get_settings().telegram_webhook_secret}
    await client.post("/api/telegram/webhook", json=tg_update(42, "hi"), headers=headers)
    await client.post("/api/telegram/webhook", json=tg_update(42, "hi"), headers=headers)  # Telegram retry
    assert await get_redis().llen("resolveai:telegram_updates") == 1


async def test_telegram_text_message_end_to_end():
    await process_update(tg_update(1, "/start"))
    sent = [m for m in get_telegram().sent if m["method"] == "sendMessage"]
    assert "UrbanCart support assistant" in sent[-1]["text"]
    async with SessionLocal() as session:
        customer = (await session.execute(select(Customer).where(Customer.telegram_user_id == 777))).scalar_one()
        assert customer.name == "Meera" and customer.telegram_username == "meera_k"


async def test_telegram_voice_note_is_transcribed_and_answered():
    await process_update(tg_update(2, voice=True))
    methods = [m["method"] for m in get_telegram().sent]
    assert "sendChatAction" in methods
    # The fake transcription asks about an order, so the agent asks the guest to verify.
    assert "email" in get_telegram().sent[-1]["text"].lower()


async def test_dashboard_flow_reply_approve_resolve(client, auth_headers):
    async with SessionLocal() as session:
        aarav = (await session.execute(select(Customer).where(Customer.email == "aarav.sharma@example.com"))).scalar_one()
        aarav.verified, aarav.telegram_chat_id = True, 9001
        await session.commit()
    await handle_customer_message(aarav.id, "I want a refund for order UC-10002, the jeans are torn", "telegram")

    tickets = (await client.get("/api/tickets?status=active", headers=auth_headers)).json()
    assert tickets[0]["pending_approvals"] == 1
    ticket_id = tickets[0]["id"]

    detail = (await client.get(f"/api/tickets/{ticket_id}", headers=auth_headers)).json()
    assert detail["approvals"][0]["status"] == "pending"
    assert any(m["role"] == "ai" for m in detail["messages"])

    approvals = (await client.get("/api/approvals", headers=auth_headers)).json()
    response = await client.post(f"/api/approvals/{approvals[0]['id']}/decide", json={"approve": True}, headers=auth_headers)
    assert response.json()["status"] == "approved"
    assert "approved" in get_telegram().sent[-1]["text"]

    reply = await client.post(
        f"/api/tickets/{ticket_id}/reply", json={"text": "Anything else I can help with?"}, headers=auth_headers
    )
    assert reply.json()["delivered"] is True

    await client.post(f"/api/tickets/{ticket_id}/resolve", headers=auth_headers)
    async with SessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        assert ticket.status == "resolved" and ticket.resolved_by == "human"
        assert (await session.execute(select(Approval.status))).scalar_one() == "approved"
    assert "inline_keyboard" in get_telegram().sent[-1]["reply_markup"]  # CSAT buttons


async def test_csat_rating_is_saved():
    async with SessionLocal() as session:
        await process_update(tg_update(5, "hi"))
        ticket = (await session.execute(select(Ticket))).scalar_one()
    await process_update({"update_id": 6, "callback_query": {"id": "cb1", "from": {"id": 777}, "data": f"csat:{ticket.id}:5"}})
    async with SessionLocal() as session:
        assert (await session.get(Ticket, ticket.id)).csat == 5


async def test_metrics_overview(client, auth_headers, guest):
    await handle_customer_message(guest, "How long does a UPI refund take?", "telegram")
    data = (await client.get("/api/metrics/overview?days=7", headers=auth_headers)).json()
    assert data["tickets"]["total"] == 1
    assert data["cost"]["total_usd"] > 0
    assert data["latency_ms"]["p95"] >= 0
    assert len(data["series"]) == 7


async def test_simulator_and_knowledge_search(client, auth_headers):
    response = await client.post("/api/simulator/message", json={"text": "Is shipping free?"}, headers=auth_headers)
    body = response.json()
    assert body["reply"] and body["customer_id"]
    search = (await client.get("/api/knowledge/search?q=express delivery cost", headers=auth_headers)).json()
    assert search[0]["doc"] == "Shipping and Delivery"
    docs = (await client.get("/api/knowledge", headers=auth_headers)).json()
    assert len(docs) == 8


async def test_auto_resolve_closes_quiet_ai_tickets(guest, monkeypatch):
    from app.config import get_settings
    from app.worker import auto_resolve_once

    await handle_customer_message(guest, "How long does a UPI refund take?", "telegram")
    monkeypatch.setattr(get_settings(), "auto_resolve_minutes", -1)
    assert await auto_resolve_once() == 1
    async with SessionLocal() as session:
        ticket = (await session.execute(select(Ticket))).scalar_one()
        assert ticket.resolved_by == "ai"
