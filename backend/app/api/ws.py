"""WebSocket that streams live events (new messages, escalations, approvals) to the dashboard."""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.auth import decode_token
from app.redis_client import get_redis
from app.services.events import CHANNEL

router = APIRouter()


@router.websocket("/api/ws")
async def events_socket(websocket: WebSocket, token: str = "") -> None:
    try:
        decode_token(token)
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(CHANNEL)

    async def forward() -> None:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])

    task = asyncio.create_task(forward())
    try:
        while True:
            await websocket.receive_text()  # keep-alive pings from the browser
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
