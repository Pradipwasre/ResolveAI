"""A deterministic, offline stand-in for a real LLM.

It lets the whole platform run without an API key: in tests, in CI, and
for a first local demo. It follows simple rules, so answers are plain,
but every code path (structured output, tool calls, embeddings, voice,
cost tracking) is exercised exactly as with the real provider.
"""

import hashlib
import math
import re
from typing import Any

from pydantic import BaseModel

from app.agent.schemas import Classification, GroundedAnswer, JudgeVerdict, RerankScores
from app.llm.base import ChatResult, LLMProvider, ModerationResult, Tier, ToolCall, Usage

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
ORDER_ID = re.compile(r"\bUC-?\d{4,6}\b", re.IGNORECASE)
WORD = re.compile(r"[a-z0-9ऀ-ॿ]+")
HINGLISH_WORDS = {"kahan", "kaha", "mera", "meri", "hai", "kab", "nahi", "kya", "paisa", "wapas", "chahiye", "karna", "hua"}
STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "my",
    "i",
    "to",
    "of",
    "and",
    "or",
    "for",
    "in",
    "on",
    "it",
    "do",
    "you",
    "can",
    "how",
    "what",
    "when",
    "where",
    "me",
    "your",
    "with",
    "be",
    "this",
    "that",
    "will",
    "does",
}


def tokens(text: str) -> list[str]:
    return WORD.findall(text.lower())


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def detect_language(text: str) -> str:
    if DEVANAGARI.search(text):
        return "hi"
    if set(tokens(text)) & HINGLISH_WORDS:
        return "hinglish"
    return "en"


def classify_text(text: str) -> Classification:
    t = text.lower()
    words = set(tokens(t))
    order = ORDER_ID.search(text)
    order_id = order.group(0).upper().replace("UC", "UC-").replace("--", "-") if order else None

    def has(*keys: str) -> bool:
        return any(k in t for k in keys)

    if has("ignore previous", "system prompt"):
        intent = "off_topic"
    elif has("human", "agent", "real person", "manager", "insaan"):
        intent = "human_request"
    elif has("refund", "money back", "paisa wapas", "पैसे वापस", "रिफंड") and not (
        has("how long", "how many days", "when will", "timeline", "policy", "how do refunds", "is the", "late", "not in my")
        and not order_id
    ):
        intent = "refund_request"
    elif has("return", "exchange", "wapas karna", "वापस"):
        intent = (
            "return_request"
            if (order_id or has("want to", "i'd like", "please", "karna")) and not has("policy", "how do", "how can", "days")
            else "knowledge"
        )
    elif has("where is", "track", "status", "kahan", "kaha", "कहाँ", "delivered yet", "not received") and (
        order_id or has("order")
    ):
        intent = "order_status"
    elif has("my orders", "last order", "recent order"):
        intent = "order_status"
    elif has("worst", "terrible", "useless", "angry", "fraud", "cheated"):
        intent = "complaint"
    elif words & {"hi", "hello", "hey", "namaste", "नमस्ते", "start"} and len(words) <= 4:
        intent = "greeting"
    elif has("password", "login", "email address", "account", "address change", "change my address"):
        intent = "account"
    elif has("weather", "cricket", "movie", "joke", "poem", "bitcoin", "politics", "recipe", "homework"):
        intent = "off_topic"
    else:
        intent = "knowledge"

    if has("worst", "terrible", "useless", "angry", "fraud", "cheated", "!!!"):
        sentiment = "angry"
    elif has("not happy", "disappointed", "late", "damaged", "broken", "bad"):
        sentiment = "negative"
    elif has("thanks", "thank you", "great", "awesome", "dhanyavad"):
        sentiment = "positive"
    else:
        sentiment = "neutral"

    subject_words = [w for w in tokens(text) if w not in STOPWORDS][:6]
    subject = " ".join(subject_words).capitalize() or "Customer conversation"
    return Classification(
        intent=intent,
        language=detect_language(text),
        sentiment=sentiment,
        order_id=order_id,
        subject=subject,
    )


def hashed_embedding(text: str, dim: int) -> list[float]:
    """Bag-of-words hashed into a fixed vector: crude, but similar texts get similar vectors."""
    vec = [0.0] * dim
    for word in tokens(text):
        if word in STOPWORDS:
            continue
        digest = hashlib.md5(word.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        vec[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def overlap(a: str, b: str) -> float:
    wa = {w for w in tokens(a) if w not in STOPWORDS}
    wb = {w for w in tokens(b) if w not in STOPWORDS}
    if not wa:
        return 0.0
    return len(wa & wb) / len(wa)


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return str(m["content"])
    return ""


def _sources_from_prompt(prompt: str) -> list[tuple[int, str]]:
    return [(int(n), body.strip()) for n, body in re.findall(r"\[(\d+)\][^\n]*\n(.*?)(?=\n\[\d+\]|\Z)", prompt, re.S)]


def describe_tool_result(raw: str) -> str:
    """Turn a tool's JSON result into a plain sentence, as a real model would."""
    import json

    data = json.loads(raw)
    if "error" in data:
        return data["error"]
    if "orders" in data:
        if not data["orders"]:
            return "I couldn't find any orders on your account."
        lines = [f"{o['order_id']}: {', '.join(o['items'])} ({o['status']})" for o in data["orders"]]
        return "Here are your recent orders:\n" + "\n".join(lines) + "\nWhich one do you need help with?"
    if "status" in data and "order_id" in data:
        text = f"Your order {data['order_id']} ({', '.join(data['items'])}) is {data['status']}."
        if data.get("courier") and data["status"] == "shipped":
            text += f" It is with {data['courier']}, tracking number {data['tracking_number']}."
        if data.get("expected_delivery") and data["status"] in {"placed", "packed", "shipped"}:
            text += f" Expected delivery: {data['expected_delivery']}."
        if data.get("delivered_on"):
            text += f" It was delivered on {data['delivered_on']}."
        return text
    if "scheduled" in data:
        return (
            (
                f"Done. A free return pickup for {data['order_id']} is scheduled by {data['pickup_by']}. "
                f"Your refund starts after the {data['refund_after']}."
            )
            if data["scheduled"]
            else data["reason"]
        )
    if "cancelled" in data:
        return f"Your order {data['order_id']} is cancelled. {data['refund']}" if data["cancelled"] else data["reason"]
    if "created" in data:
        if not data["created"]:
            return data["reason"]
        if data.get("approved"):
            return f"Your refund of Rs {data['amount_inr']:,.0f} is approved."
        return (
            f"I've raised a refund request for Rs {data['amount_inr']:,.0f}. "
            "A team member reviews refund requests, usually within a few hours, and you'll get an update here."
        )
    return "Done."


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, embedding_dim: int = 1536):
        self.embedding_dim = embedding_dim

    def model_for(self, tier: Tier) -> str:
        return "fake-smart" if tier == "smart" else "fake-fast"

    def _result(self, messages: list[dict[str, Any]], tier: Tier, text: str = "", **kw: Any) -> ChatResult:
        prompt_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        return ChatResult(
            text=text,
            model=self.model_for(tier),
            usage=Usage(input_tokens=prompt_tokens, output_tokens=estimate_tokens(text or str(kw.get("parsed", "")))),
            **kw,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: Tier,
        purpose: str,
        response_model: type[BaseModel] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        user = _last_user(messages)
        if response_model is Classification:
            return self._result(messages, tier, parsed=classify_text(user.split("Customer message:")[-1]))
        if response_model is GroundedAnswer:
            return self._result(messages, tier, parsed=self._grounded_answer(user))
        if response_model is JudgeVerdict:
            return self._result(messages, tier, parsed=self._judge(user))
        if response_model is RerankScores:
            question = user.split("Question:")[-1].split("\n")[0]
            passages = re.findall(r"Passage \d+:\n(.*?)(?=\nPassage \d+:|\Z)", user, re.S)
            return self._result(messages, tier, parsed=RerankScores(scores=[overlap(question, p) for p in passages]))
        if tools:
            return self._tool_step(messages, tier)
        if purpose == "summarize":
            lines = [line for line in user.splitlines() if line.strip()][-6:]
            return self._result(messages, tier, text="Earlier conversation: " + " | ".join(lines)[:600])
        return self._result(messages, tier, text="Thanks for reaching out. How can I help you with your order today?")

    def _grounded_answer(self, prompt: str) -> GroundedAnswer:
        question = prompt.split("Customer question:")[-1].strip()
        sources = _sources_from_prompt(prompt.split("Customer question:")[0])
        if not sources:
            return GroundedAnswer(answer="I don't have that information.", cited_sources=[], confidence=0.0)
        scored = sorted(sources, key=lambda s: overlap(question, s[1]), reverse=True)
        number, body = scored[0]
        body = body.split("\n", 1)[-1]  # drop the "Title - Section" line
        sentences = re.split(r"(?<=[.!?])\s+", body)
        best = sorted(sentences, key=lambda s: overlap(question, s), reverse=True)[:2]
        answer = " ".join(s.strip() for s in best if s.strip())
        confidence = min(1.0, 0.45 + overlap(question, body))
        return GroundedAnswer(answer=f"{answer} [{number}]", cited_sources=[number], confidence=round(confidence, 2))

    def _judge(self, prompt: str) -> JudgeVerdict:
        question = prompt.split("Question:")[-1].split("Context:")[0]
        context = prompt.split("Context:")[-1].split("Answer:")[0]
        answer = prompt.split("Answer:")[-1]
        return JudgeVerdict(
            faithfulness=round(min(1.0, overlap(answer, context) + 0.1), 2),
            relevance=round(min(1.0, overlap(question, answer) + 0.3), 2),
            reasoning="Lexical overlap estimate (fake provider).",
        )

    def _tool_step(self, messages: list[dict[str, Any]], tier: Tier) -> ChatResult:
        tool_messages = [m for m in messages if m["role"] == "tool"]
        user = _last_user(messages)
        if tool_messages:
            return self._result(messages, tier, text=describe_tool_result(str(tool_messages[-1]["content"])))
        order = ORDER_ID.search(user)
        order_id = order.group(0).upper() if order else None
        c = classify_text(user)
        if c.intent == "refund_request" and order_id:
            call = ToolCall(id="call_1", name="request_refund", arguments={"order_id": order_id, "reason": user[:200]})
        elif c.intent == "return_request" and order_id:
            call = ToolCall(id="call_1", name="request_return", arguments={"order_id": order_id, "reason": user[:200]})
        elif order_id:
            call = ToolCall(id="call_1", name="get_order", arguments={"order_id": order_id})
        else:
            call = ToolCall(id="call_1", name="list_orders", arguments={})
        return self._result(messages, tier, tool_calls=[call])

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage, str]:
        vectors = [hashed_embedding(t, self.embedding_dim) for t in texts]
        return vectors, Usage(input_tokens=sum(estimate_tokens(t) for t in texts)), "fake-embed"

    async def transcribe(self, audio: bytes, filename: str) -> tuple[str, Usage, str]:
        return "Where is my order UC-10001?", Usage(), "fake-transcribe"

    async def speak(self, text: str) -> tuple[bytes, Usage, str]:
        return b"", Usage(input_tokens=estimate_tokens(text)), "fake-tts"

    async def moderate(self, text: str) -> ModerationResult:
        return ModerationResult(flagged=False)
