"""Database tables.

Customers, orders and knowledge chunks hold the client's business data.
Tickets, messages, approvals, LLM calls, guardrail events and feedback
hold everything the support platform produces, so every metric on the
dashboard can be computed with SQL.
"""

from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TicketStatus(StrEnum):
    OPEN = "open"  # AI is handling it
    NEEDS_HUMAN = "needs_human"  # escalated, waiting for an agent
    HUMAN = "human"  # an agent took over
    RESOLVED = "resolved"


class MessageRole(StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    AGENT = "agent"
    SYSTEM = "system"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Agent(Base, TimestampMixin):
    """A support team member who logs in to the dashboard."""

    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(20), default="standard")  # standard or plus
    language: Mapped[str] = mapped_column(String(10), default="en")
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_summary: Mapped[str] = mapped_column(Text, default="")

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Order(Base, TimestampMixin):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # e.g. UC-10001
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))  # placed, shipped, delivered, cancelled, refunded
    items: Mapped[list] = mapped_column(JSONB)
    total_inr: Mapped[float] = mapped_column(Float)
    payment_method: Mapped[str] = mapped_column(String(30))
    courier: Mapped[str | None] = mapped_column(String(60))
    tracking_number: Mapped[str | None] = mapped_column(String(60))
    expected_delivery: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # telegram or web
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.OPEN, index=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # low, normal, high
    intent: Mapped[str | None] = mapped_column(String(40))
    sentiment: Mapped[str | None] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(10))
    subject: Mapped[str] = mapped_column(String(200), default="New conversation")
    escalation_reason: Mapped[str | None] = mapped_column(String(255))
    resolved_by: Mapped[str | None] = mapped_column(String(10))  # ai or human
    csat: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="ticket", order_by="Message.id")


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)  # PII-masked text
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    sources: Mapped[list | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class Approval(Base, TimestampMixin):
    """An action the AI proposed that needs a human decision (e.g. a refund)."""

    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    action: Mapped[str] = mapped_column(String(20))  # refund or return
    amount_inr: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.PENDING, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[Ticket] = relationship()


class LLMCall(Base, TimestampMixin):
    """One row per model call: the source of every cost and latency metric."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(Integer, index=True)
    purpose: Mapped[str] = mapped_column(String(40))  # classify, answer, summarize, embed...
    model: Mapped[str] = mapped_column(String(60))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class GuardrailEvent(Base, TimestampMixin):
    __tablename__ = "guardrail_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    stage: Mapped[str] = mapped_column(String(10))  # input or output
    kind: Mapped[str] = mapped_column(String(40))  # pii, injection, moderation, rate_limit, budget, grounding
    action: Mapped[str] = mapped_column(String(20))  # masked, blocked, escalated, retried
    detail: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(255))  # ai or an agent email
    action: Mapped[str] = mapped_column(String(60))
    ticket_id: Mapped[int | None] = mapped_column(Integer, index=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class KnowledgeChunk(Base, TimestampMixin):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(120), index=True)
    doc_title: Mapped[str] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(TSVECTOR)

    __table_args__ = (
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class EvalRun(Base, TimestampMixin):
    __tablename__ = "eval_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    metrics: Mapped[dict] = mapped_column(JSONB)
    passed: Mapped[bool] = mapped_column(Boolean)
