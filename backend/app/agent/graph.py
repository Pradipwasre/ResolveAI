"""The support agent, built as a LangGraph state machine.

    classify -> route -> greeting | verify | account (tools) | knowledge (RAG)
                         | escalate | off_topic
             -> guard (output checks) -> END

Every branch is an explicit node, so behaviour is predictable, testable
and visible in traces. The LLM decides *what* the customer wants and
*how* to phrase answers; the graph decides *what is allowed*.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import ACCOUNT_SYSTEM, ANSWER_SYSTEM, CLASSIFY_SYSTEM, template
from app.agent.schemas import Classification, GroundedAnswer
from app.agent.tools import TOOL_SCHEMAS, CustomerTools
from app.config import get_settings
from app.guardrails.output import check_output
from app.llm import gateway
from app.logging_setup import log
from app.models import Customer, GuardrailEvent, Ticket
from app.rag.retriever import hybrid_search
from app.redis_client import get_redis
from app.services.memory import trim_to_budget

ACCOUNT_INTENTS = {"order_status", "return_request", "refund_request", "account"}
MAX_TOOL_STEPS = 4


class AgentState(TypedDict, total=False):
    ticket_id: int
    customer_id: int
    customer_name: str
    customer_tier: str
    verified: bool
    memory: str
    text: str  # current customer message, PII-masked
    emails: list[str]  # emails found in the raw message (for verification only)
    history: list[dict[str, str]]
    classification: dict[str, Any]
    language: str
    reply: str
    reply_prefix: str  # e.g. "your account is verified", shown before the main reply
    reply_kind: str  # template, rag, account, cache
    sources: list[dict[str, Any]]
    confidence: float | None
    escalate_reason: str | None
    approvals_created: list[int]
    actions: list[str]
    retried: bool
    route: str


@dataclass
class AgentContext:
    session: AsyncSession


def _client() -> str:
    return get_settings().client_name


def _lang(state: AgentState) -> str:
    return state.get("language") or state.get("classification", {}).get("language", "en")


async def _guard_event(session: AsyncSession, ticket_id: int, stage: str, kind: str, action: str, detail: str = "") -> None:
    session.add(GuardrailEvent(ticket_id=ticket_id, stage=stage, kind=kind, action=action, detail=detail[:500]))
    await session.commit()


# ---------------------------------------------------------------- nodes


async def classify(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    history = trim_to_budget(state.get("history", [])[-6:], 600)
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    result = await gateway.chat(
        [
            {"role": "system", "content": CLASSIFY_SYSTEM.format(client=_client())},
            {"role": "user", "content": f"Recent conversation:\n{convo or '(none)'}\n\nCustomer message: {state['text']}"},
        ],
        purpose="classify",
        response_model=Classification,
    )
    parsed = result.parsed or Classification(intent="knowledge", language="en", sentiment="neutral", subject="Customer question")
    return {"classification": parsed.model_dump(), "language": parsed.language}


def route(state: AgentState) -> str:
    c = state["classification"]
    intent = c["intent"]
    if state.get("emails") and not state.get("verified"):
        return "verify"
    if intent == "human_request":
        return "escalate"
    if intent == "complaint":
        return "account" if c.get("order_id") and state.get("verified") else "escalate"
    if c["sentiment"] == "angry" and intent not in ACCOUNT_INTENTS:
        return "escalate"
    if intent in ACCOUNT_INTENTS:
        return "account" if state.get("verified") else "verify"
    if intent == "greeting":
        return "greeting"
    if intent == "off_topic":
        return "off_topic"
    return "knowledge"


async def greeting(state: AgentState) -> AgentState:
    name = f" {state['customer_name'].split()[0]}" if state.get("verified") and state.get("customer_name") else ""
    return {"reply": template("greeting", _lang(state), _client(), name), "reply_kind": "template"}


async def off_topic(state: AgentState) -> AgentState:
    return {"reply": template("off_topic", _lang(state), _client()), "reply_kind": "template"}


async def escalate(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    reason = (
        state.get("escalate_reason")
        or f"intent={state['classification']['intent']}, sentiment={state['classification']['sentiment']}"
    )
    return {"reply": template("escalate", _lang(state), _client()), "reply_kind": "template", "escalate_reason": reason}


def _pending_key(ticket_id: int) -> str:
    return f"pending:ticket:{ticket_id}"


async def verify(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Link the chat to a store account using the registered email.

    Demo-grade verification. In production, send a one-time code to that
    email and confirm it before linking.
    """
    session = runtime.context.session
    redis = get_redis()
    lang = _lang(state)
    emails = state.get("emails") or []
    if not emails:
        await redis.set(_pending_key(state["ticket_id"]), state["text"], ex=3600)
        return {"reply": template("verify", lang, _client()), "reply_kind": "template", "route": "done"}

    account = (await session.execute(select(Customer).where(Customer.email == emails[0].lower()))).scalar_one_or_none()
    if account is None:
        return {"reply": template("verify_failed", lang, _client()), "reply_kind": "template", "route": "done"}

    guest = await session.get(Customer, state["customer_id"])
    if guest is not None and guest.id != account.id:
        tg_user, tg_chat, tg_name = guest.telegram_user_id, guest.telegram_chat_id, guest.telegram_username
        guest.telegram_user_id = None
        await session.flush()  # free the unique Telegram id before moving it
        account.telegram_user_id, account.telegram_chat_id, account.telegram_username = tg_user, tg_chat, tg_name
        await session.execute(update(Ticket).where(Ticket.customer_id == guest.id).values(customer_id=account.id))
        await session.flush()
        await session.delete(guest)
    account.verified = True
    await session.commit()
    gateway.current_customer_id.set(account.id)

    pending = await redis.getdel(_pending_key(state["ticket_id"]))
    prefix = template("verified", lang, _client(), account.name.split()[0])
    update_state: AgentState = {
        "customer_id": account.id,
        "customer_name": account.name,
        "customer_tier": account.tier,
        "verified": True,
        "memory": account.memory_summary,
        "emails": [],
        "reply": prefix,
        "reply_kind": "template",
    }
    if pending:
        update_state.update({"text": pending, "route": "classify", "reply_prefix": prefix})
    else:
        update_state["route"] = "done"
    return update_state


def after_verify(state: AgentState) -> str:
    return "classify" if state.get("route") == "classify" else "guard"


async def account(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    session = runtime.context.session
    tools = CustomerTools(session, state["customer_id"], state["ticket_id"])
    system = ACCOUNT_SYSTEM.format(
        client=_client(),
        name=state.get("customer_name", "Customer"),
        tier=state.get("customer_tier", "standard"),
        today=datetime.now(UTC).date().isoformat(),
        language=_lang(state),
        memory=state.get("memory") or "none",
    )
    history = trim_to_budget(state.get("history", []), get_settings().max_context_tokens)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, *history, {"role": "user", "content": state["text"]}]
    tier = "smart" if state["classification"]["sentiment"] in {"negative", "angry"} else "fast"
    reply = ""
    for _ in range(MAX_TOOL_STEPS):
        result = await gateway.chat(messages, purpose="account", tier=tier, tools=TOOL_SCHEMAS)
        if not result.tool_calls:
            reply = result.text
            break
        messages.append(
            {
                "role": "assistant",
                "content": result.text or None,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in result.tool_calls
                ],
            }
        )
        for call in result.tool_calls:
            output = await tools.run(call.name, call.arguments)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
    if not reply:
        return {
            "escalate_reason": "tool_loop_limit",
            "approvals_created": tools.approvals_created,
            "actions": tools.actions,
            "reply": template("escalate", _lang(state), _client()),
            "reply_kind": "template",
        }
    return {"reply": reply, "reply_kind": "account", "approvals_created": tools.approvals_created, "actions": tools.actions}


def _cache_key(text: str, language: str) -> str:
    normal = " ".join(text.lower().split())
    return "answer_cache:" + hashlib.sha256(f"{language}|{normal}".encode()).hexdigest()


async def knowledge(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    session = runtime.context.session
    settings = get_settings()
    redis = get_redis()
    lang = _lang(state)
    day = datetime.now(UTC).strftime("%Y-%m-%d")

    cache_key = _cache_key(state["text"], lang)
    if not state.get("retried") and (cached := await redis.get(cache_key)):
        await redis.incr(f"cache:{day}:hits")
        data = json.loads(cached)
        return {"reply": data["reply"], "sources": data["sources"], "confidence": data["confidence"], "reply_kind": "cache"}
    await redis.incr(f"cache:{day}:misses")

    chunks = await hybrid_search(session, state["text"])
    best = max((c.similarity for c in chunks), default=0.0)
    if not chunks or best < settings.retrieval_threshold:
        return {
            "escalate_reason": f"low_retrieval_similarity={best:.2f}",
            "reply": template("escalate", lang, _client()),
            "reply_kind": "template",
            "confidence": round(best, 2),
        }

    sources_block = "\n\n".join(f"[{i + 1}] {c.doc_title} / {c.section}\n{c.content}" for i, c in enumerate(chunks))
    long_or_upset = len(state["text"]) > 300 or state["classification"]["sentiment"] in {"negative", "angry"}
    tier = "smart" if state.get("retried") or long_or_upset else "fast"
    history = trim_to_budget(state.get("history", []), settings.max_context_tokens // 2)
    result = await gateway.chat(
        [
            {"role": "system", "content": ANSWER_SYSTEM.format(client=_client(), language=lang)},
            *history,
            {"role": "user", "content": f"<sources>\n{sources_block}\n</sources>\n\nCustomer question: {state['text']}"},
        ],
        purpose="answer",
        tier=tier,
        response_model=GroundedAnswer,
    )
    answer = result.parsed
    if answer is None or answer.confidence < settings.confidence_threshold:
        confidence = answer.confidence if answer else 0.0
        return {
            "escalate_reason": f"low_answer_confidence={confidence:.2f}",
            "reply": template("escalate", lang, _client()),
            "reply_kind": "template",
            "confidence": confidence,
        }

    cited = [chunks[n - 1] for n in answer.cited_sources if 1 <= n <= len(chunks)] or chunks[:1]
    sources = [{"n": chunks.index(c) + 1, "doc": c.doc_title, "section": c.section} for c in cited]
    # Lexical grounding only makes sense when answer and sources share a language.
    check = check_output(answer.answer, sources=[c.content for c in cited], min_grounding=0.2 if lang == "en" else 0.0)
    if not check.passed:
        await _guard_event(
            session,
            state["ticket_id"],
            "output",
            "grounding",
            "escalated" if state.get("retried") else "retried",
            ",".join(check.issues),
        )
        if not state.get("retried"):
            return {"retried": True, "route": "knowledge"}
        return {
            "escalate_reason": "failed_grounding_check",
            "reply": template("escalate", lang, _client()),
            "reply_kind": "template",
            "confidence": answer.confidence,
        }

    await redis.set(
        cache_key,
        json.dumps({"reply": check.text, "sources": sources, "confidence": answer.confidence}),
        ex=settings.answer_cache_ttl_seconds,
    )
    return {"reply": check.text, "sources": sources, "confidence": answer.confidence, "reply_kind": "rag", "route": "done"}


def after_knowledge(state: AgentState) -> str:
    return "knowledge" if state.get("route") == "knowledge" else "guard"


async def guard(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Final output check on every reply, whatever branch produced it."""
    check = check_output(state.get("reply", ""))
    if check.issues:
        await _guard_event(
            runtime.context.session,
            state["ticket_id"],
            "output",
            "output_check",
            "masked" if check.passed else "blocked",
            ",".join(check.issues),
        )
    if not check.passed:
        log.warning("output_blocked", issues=check.issues, ticket_id=state["ticket_id"])
        return {"reply": template("escalate", _lang(state), _client()), "escalate_reason": "output_blocked"}
    prefix = state.get("reply_prefix")
    return {"reply": f"{prefix}\n\n{check.text}" if prefix else check.text}


# ---------------------------------------------------------------- graph


def build_graph():
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("classify", classify)
    graph.add_node("greeting", greeting)
    graph.add_node("off_topic", off_topic)
    graph.add_node("escalate", escalate)
    graph.add_node("verify", verify)
    graph.add_node("account", account)
    graph.add_node("knowledge", knowledge)
    graph.add_node("guard", guard)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route,
        ["greeting", "off_topic", "escalate", "verify", "account", "knowledge"],
    )
    graph.add_conditional_edges("verify", after_verify, ["classify", "guard"])
    graph.add_conditional_edges("knowledge", after_knowledge, ["knowledge", "guard"])
    for node in ("greeting", "off_topic", "escalate", "account"):
        graph.add_edge(node, "guard")
    graph.add_edge("guard", END)
    return graph.compile(name="resolveai_support_agent")


support_agent = build_graph()
