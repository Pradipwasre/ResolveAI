"""Telegram webhook endpoint and webhook registration."""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.auth import current_agent
from app.channels.telegram import enqueue_update, get_telegram
from app.config import get_settings

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")) -> dict:
    # Telegram sends the secret we registered; anything else is not from Telegram.
    if not hmac.compare_digest(x_telegram_bot_api_secret_token, get_settings().telegram_webhook_secret):
        raise HTTPException(403, "Bad secret")
    update = await request.json()
    await enqueue_update(update)  # answer fast; the worker does the slow part
    return {"ok": True}


@router.post("/set-webhook", dependencies=[Depends(current_agent)])
async def set_webhook() -> dict:
    settings = get_settings()
    if not settings.public_base_url:
        raise HTTPException(400, "PUBLIC_BASE_URL is not set")
    url = f"{settings.public_base_url.rstrip('/')}/api/telegram/webhook"
    result = await get_telegram().set_webhook(url, settings.telegram_webhook_secret)
    return {"url": url, "result": result}
