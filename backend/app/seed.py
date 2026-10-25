"""Demo data for the client: support agents, customers and orders.

Runs once on startup when the tables are empty. Dates are relative to
today so the return windows and delivery dates always make sense.
"""

from datetime import UTC, datetime, timedelta

import bcrypt
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.logging_setup import log
from app.models import Agent, Customer, Order

CUSTOMERS = [
    ("Aarav Sharma", "aarav.sharma@example.com", "+91 98200 11111", "plus", "en"),
    ("Priya Iyer", "priya.iyer@example.com", "+91 98450 22222", "standard", "en"),
    ("Rohan Verma", "rohan.verma@example.com", "+91 99100 33333", "standard", "hi"),
    ("Sneha Patel", "sneha.patel@example.com", "+91 97230 44444", "plus", "en"),
    ("Vikram Singh", "vikram.singh@example.com", "+91 98110 55555", "standard", "hinglish"),
]


def _orders(now: datetime) -> list[dict]:
    d = lambda days: now - timedelta(days=days)  # noqa: E731
    return [
        dict(
            id="UC-10001",
            c=0,
            status="shipped",
            items=[{"name": "boAt Airdopes 141 earbuds", "category": "electronics", "qty": 1}],
            total=1299,
            pay="UPI",
            courier="Delhivery",
            track="DLV55123401",
            placed=d(3),
            eta=now + timedelta(days=1),
        ),
        dict(
            id="UC-10002",
            c=0,
            status="delivered",
            items=[{"name": "Levi's 511 slim jeans", "category": "fashion", "qty": 1}],
            total=2599,
            pay="Credit card",
            courier="Blue Dart",
            track="BD99812345",
            placed=d(9),
            delivered=d(5),
        ),
        dict(
            id="UC-10003",
            c=1,
            status="delivered",
            items=[{"name": "Prestige induction cooktop", "category": "electronics", "qty": 1}],
            total=3499,
            pay="Cash on Delivery",
            courier="Ekart",
            track="EK77120034",
            placed=d(6),
            delivered=d(2),
        ),
        dict(
            id="UC-10004",
            c=1,
            status="placed",
            items=[{"name": "Cotton bedsheet set", "category": "home", "qty": 2}],
            total=1798,
            pay="UPI",
            courier=None,
            track=None,
            placed=d(0),
            eta=now + timedelta(days=5),
        ),
        dict(
            id="UC-10005",
            c=2,
            status="delivered",
            items=[{"name": "Puma running shoes", "category": "fashion", "qty": 1}],
            total=3999,
            pay="Debit card",
            courier="Delhivery",
            track="DLV55987766",
            placed=d(25),
            delivered=d(20),
        ),
        dict(
            id="UC-10006",
            c=2,
            status="shipped",
            items=[{"name": "Philips air fryer", "category": "electronics", "qty": 1}],
            total=6499,
            pay="Credit card EMI",
            courier="Blue Dart",
            track="BD99855501",
            placed=d(4),
            eta=d(1),
        ),
        dict(
            id="UC-10007",
            c=3,
            status="delivered",
            items=[{"name": "Noise smartwatch", "category": "electronics", "qty": 1}],
            total=2999,
            pay="UPI",
            courier="Ekart",
            track="EK77199900",
            placed=d(5),
            delivered=d(1),
        ),
        dict(
            id="UC-10008",
            c=4,
            status="packed",
            items=[{"name": "Atomic Habits (paperback)", "category": "books", "qty": 1}],
            total=399,
            pay="Cash on Delivery",
            courier=None,
            track=None,
            placed=d(1),
            eta=now + timedelta(days=4),
        ),
    ]


async def seed_if_empty() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        if not (await session.execute(select(func.count(Agent.id)))).scalar_one():
            hashed = bcrypt.hashpw(settings.admin_password.encode(), bcrypt.gensalt()).decode()
            session.add(Agent(email=settings.admin_email.lower(), name="Support Admin", password_hash=hashed))
            log.info("seeded_admin", email=settings.admin_email)
        if not (await session.execute(select(func.count(Customer.id)))).scalar_one():
            now = datetime.now(UTC)
            customers = [Customer(name=n, email=e, phone=p, tier=t, language=lang) for n, e, p, t, lang in CUSTOMERS]
            session.add_all(customers)
            await session.flush()
            for o in _orders(now):
                session.add(
                    Order(
                        id=o["id"],
                        customer_id=customers[o["c"]].id,
                        status=o["status"],
                        items=o["items"],
                        total_inr=o["total"],
                        payment_method=o["pay"],
                        courier=o["courier"],
                        tracking_number=o["track"],
                        created_at=o["placed"],
                        expected_delivery=o.get("eta"),
                        delivered_at=o.get("delivered"),
                    )
                )
            log.info("seeded_customers", customers=len(customers))
        await session.commit()
