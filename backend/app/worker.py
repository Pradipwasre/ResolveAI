"""Background worker.

- Consumes Telegram updates from the Redis queue (filled by the webhook,
  or by polling Telegram directly with --poll when running locally).
- Closes AI-handled tickets that have gone quiet and asks for a rating.

Run: python -m app.worker            (webhook mode, used on AWS)
     python -m app.worker --poll     (local mode: no public URL needed)
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent.prompts import template
from app.channels.telegram import (
    CSAT_KEYBOARD,
    QUEUE,
    enqueue_update,
    get_telegram,
    process_update,
    send_to_customer,
)
from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import configure_logging, log
from app.models import Customer, Ticket, TicketStatus
from app.redis_client import get_redis
from app.services import events
from app.startup import run_startup_tasks

CONCURRENCY = 8


async def consume(stop: asyncio.Event) -> None:
    redis = get_redis()
    limiter = asyncio.Semaphore(CONCURRENCY)
    tasks: set[asyncio.Task] = set()

    async def run(update: dict) -> None:
        async with limiter:
            try:
                await process_update(update)
            except Exception as exc:
                log.exception("update_failed", update_id=update.get("update_id"), error=str(exc))

    while not stop.is_set():
        item = await redis.brpop([QUEUE], timeout=2)
        if item is None:
            continue
        task = asyncio.create_task(run(json.loads(item[1])))
        tasks.add(task)
        task.add_done_callback(tasks.discard)


async def poll_telegram(stop: asyncio.Event) -> None:
    telegram = get_telegram()
    if telegram.offline:
        log.warning("polling_disabled", reason="TELEGRAM_BOT_TOKEN is empty")
        return
    await telegram.delete_webhook()
    offset = 0
    log.info("telegram_polling_started")
    while not stop.is_set():
        try:
            for update in await telegram.get_updates(offset):
                offset = update["update_id"] + 1
                await enqueue_update(update)
        except Exception as exc:
            log.warning("polling_error", error=str(exc))
            await asyncio.sleep(3)


async def auto_resolve_once() -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.auto_resolve_minutes)
    closed = 0
    async with SessionLocal() as session:
        tickets = (
            (await session.execute(select(Ticket).where(Ticket.status == TicketStatus.OPEN, Ticket.updated_at < cutoff)))
            .scalars()
            .all()
        )
        for ticket in tickets:
            ticket.status = TicketStatus.RESOLVED
            ticket.resolved_by = "ai"
            ticket.resolved_at = datetime.now(UTC)
            closed += 1
            customer = await session.get(Customer, ticket.customer_id)
            await session.commit()
            await events.publish("ticket_resolved", ticket_id=ticket.id, by="ai")
            if customer:
                await send_to_customer(
                    customer, template("csat", ticket.language, settings.client_name), CSAT_KEYBOARD(ticket.id)
                )
    return closed


async def auto_resolve_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            if closed := await auto_resolve_once():
                log.info("auto_resolved", tickets=closed)
        except Exception as exc:
            log.warning("auto_resolve_failed", error=str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass


async def main(poll: bool) -> None:
    configure_logging(get_settings().log_level)
    await run_startup_tasks()
    stop = asyncio.Event()
    jobs = [consume(stop), auto_resolve_loop(stop)]
    if poll:
        jobs.append(poll_telegram(stop))
    log.info("worker_started", poll=poll)
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll", action="store_true", help="poll Telegram instead of using a webhook")
    args = parser.parse_args()
    asyncio.run(main(args.poll))
