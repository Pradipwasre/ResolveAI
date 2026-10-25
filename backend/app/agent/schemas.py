"""Structured outputs the LLM must return. Pydantic validates every response."""

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "greeting",
    "knowledge",  # a question the help articles can answer (shipping, returns policy, payments...)
    "order_status",
    "return_request",
    "refund_request",
    "account",
    "human_request",
    "complaint",
    "off_topic",
]


class Classification(BaseModel):
    intent: Intent
    language: Literal["en", "hi", "hinglish"] = Field(
        description="hi = Hindi in Devanagari, hinglish = Hindi written in Latin letters"
    )
    sentiment: Literal["positive", "neutral", "negative", "angry"]
    order_id: str | None = Field(default=None, description="Order id like UC-10001 if mentioned")
    subject: str = Field(description="A 3 to 8 word ticket subject in English")


class GroundedAnswer(BaseModel):
    answer: str = Field(description="The reply to the customer, in their language")
    cited_sources: list[int] = Field(description="Numbers of the sources used, e.g. [1, 3]")
    confidence: float = Field(ge=0, le=1, description="How fully the sources answer the question")


class JudgeVerdict(BaseModel):
    faithfulness: float = Field(ge=0, le=1, description="Share of claims supported by the context")
    relevance: float = Field(ge=0, le=1, description="How well the answer addresses the question")
    reasoning: str


class RerankScores(BaseModel):
    scores: list[float] = Field(description="Relevance score 0 to 1 for each passage, in order")
