"""Live events for the dashboard, sent over Redis pub/sub.

Any API or worker process can publish; every API process forwards events
to its connected dashboards over WebSocket. This works with one container
locally and with several containers on AWS.
"""

import json
from datetime import UTC, datetime
from typing import Any

from app.logging_setup import log
from app.redis_client import get_redis

CHANNEL = "resolveai:events"


async def publish(event: str, **data: Any) -> None:
    payload = {"event": event, "at": datetime.now(UTC).isoformat(), **data}
    try:
        await get_redis().publish(CHANNEL, json.dumps(payload, default=str))
    except Exception as exc:
        log.warning("event_publish_failed", error=str(exc))
