"""Telegram channel: a thin adapter between the Bot API and the conversation service.

Flow: Telegram -> webhook (or local polling) -> Redis queue -> worker ->
process_update() -> handle_customer_message() -> reply as text or voice.

Without a bot token the client runs in "offline" mode and records what it
would have sent, which is what the tests use.
"""

import json
from typing import Any

import httpx
from sqlalchemy import select

from app.agent.prompts import template
from app.config import get_settings
from app.db import SessionLocal
from app.llm import gateway
from app.logging_setup import log
from app.models import Customer, Ticket
from app.redis_client import get_redis
from app.services import events
from app.services.conversation import handle_customer_message

QUEUE = "resolveai:telegram_updates"
MAX_VOICE_SECONDS = 120


class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.offline = not token
        self.sent: list[dict[str, Any]] = []  # offline mode record
        self._http = httpx.AsyncClient(timeout=30)

    @property
    def api(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def call(self, method: str, data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> Any:
        if self.offline:
            self.sent.append({"method": method, **(data or {}), "has_file": bool(files)})
            return {"ok": True}
        response = await self._http.post(f"{self.api}/{method}", data=data, files=files)
        body = response.json()
        if not body.get("ok"):
            log.warning("telegram_api_error", method=method, description=body.get("description"))
        return body.get("result")

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        data: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        await self.call("sendMessage", data)

    async def send_voice(self, chat_id: int, audio: bytes, caption: str) -> None:
        await self.call(
            "sendVoice", {"chat_id": chat_id, "caption": caption[:1024]}, files={"voice": ("reply.ogg", audio, "audio/ogg")}
        )

    async def send_typing(self, chat_id: int, action: str = "typing") -> None:
        await self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def download_file(self, file_id: str) -> bytes:
        if self.offline:
            return b"offline-audio"
        info = await self.call("getFile", {"file_id": file_id})
        response = await self._http.get(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}")
        response.raise_for_status()
        return response.content

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def set_webhook(self, url: str, secret: str) -> Any:
        return await self.call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret,
                "allowed_updates": json.dumps(["message", "callback_query"]),
                "drop_pending_updates": "true",
            },
        )

    async def delete_webhook(self) -> Any:
        return await self.call("deleteWebhook", {"drop_pending_updates": "false"})

    async def get_updates(self, offset: int, wait_seconds: int = 25) -> list[dict[str, Any]]:
        response = await self._http.post(
            f"{self.api}/getUpdates",
            data={"offset": offset, "timeout": wait_seconds, "allowed_updates": json.dumps(["message", "callback_query"])},
            timeout=wait_seconds + 10,
        )
        return response.json().get("result", [])


_client: TelegramClient | None = None


def get_telegram() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient(get_settings().telegram_bot_token)
    return _client


CSAT_KEYBOARD = lambda ticket_id: {  # noqa: E731
    "inline_keyboard": [[{"text": str(n), "callback_data": f"csat:{ticket_id}:{n}"} for n in range(1, 6)]]
}


async def enqueue_update(update: dict[str, Any]) -> bool:
    """Queue an update once. Telegram retries deliveries, so we dedupe on update_id."""
    redis = get_redis()
    if not await redis.set(f"tg:update:{update.get('update_id')}", 1, nx=True, ex=86400):
        return False
    await redis.lpush(QUEUE, json.dumps(update))
    return True


async def _customer_for(user: dict[str, Any], chat_id: int) -> Customer:
    async with SessionLocal() as session:
        customer = (await session.execute(select(Customer).where(Customer.telegram_user_id == user["id"]))).scalar_one_or_none()
        if customer is None:
            name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Telegram user"
            customer = Customer(
                name=name,
                telegram_user_id=user["id"],
                telegram_chat_id=chat_id,
                telegram_username=user.get("username"),
                language="en",
            )
            session.add(customer)
            await session.commit()
        elif customer.telegram_chat_id != chat_id:
            customer.telegram_chat_id = chat_id
            await session.commit()
        return customer


async def _handle_csat(callback: dict[str, Any]) -> None:
    telegram = get_telegram()
    try:
        _, ticket_id, score = callback["data"].split(":")
    except ValueError:
        return
    async with SessionLocal() as session:
        ticket = await session.get(Ticket, int(ticket_id))
        customer = await session.get(Customer, ticket.customer_id) if ticket else None
        if ticket and customer and customer.telegram_user_id == callback["from"]["id"]:
            ticket.csat = int(score)
            await session.commit()
            await events.publish("csat", ticket_id=ticket.id, score=int(score))
            await telegram.answer_callback(callback["id"], template("csat_thanks", ticket.language, get_settings().client_name))
            return
    await telegram.answer_callback(callback["id"])


async def process_update(update: dict[str, Any]) -> None:
    telegram = get_telegram()
    if callback := update.get("callback_query"):
        if str(callback.get("data", "")).startswith("csat:"):
            await _handle_csat(callback)
        return

    message = update.get("message")
    if not message or message.get("chat", {}).get("type") != "private":
        return
    chat_id = message["chat"]["id"]
    customer = await _customer_for(message["from"], chat_id)

    text = message.get("text")
    voice = message.get("voice") or message.get("audio")
    is_voice = False
    if text == "/start":
        text = "hello"
    elif voice:
        if voice.get("duration", 0) > MAX_VOICE_SECONDS:
            await telegram.send_message(chat_id, "Please keep voice notes under 2 minutes.")
            return
        await telegram.send_typing(chat_id, "record_voice")
        audio = await telegram.download_file(voice["file_id"])
        gateway.current_customer_id.set(customer.id)
        text = await gateway.transcribe(audio, "voice.ogg", float(voice.get("duration", 0)))
        is_voice = True
    if not text:
        await telegram.send_message(chat_id, "I can read text messages and listen to voice notes. Please send one of those.")
        return

    await telegram.send_typing(chat_id)
    result = await handle_customer_message(customer.id, text, channel="telegram", is_voice=is_voice)
    if result.reply is None:
        return
    if is_voice:
        try:
            audio = await gateway.speak(result.reply)
            if audio:
                await telegram.send_voice(chat_id, audio, caption=result.reply)
                return
        except Exception as exc:  # voice is a nice-to-have: fall back to text
            log.warning("tts_failed", error=str(exc))
    await telegram.send_message(chat_id, result.reply)


async def send_to_customer(customer: Customer, text: str, reply_markup: dict | None = None) -> bool:
    """Used by the dashboard when a human agent replies or resolves a ticket."""
    if customer.telegram_chat_id is None:
        return False
    await get_telegram().send_message(customer.telegram_chat_id, text, reply_markup)
    return True
