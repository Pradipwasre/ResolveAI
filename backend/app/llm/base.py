"""Provider-neutral LLM interface.

The agent only talks to this interface, so the provider can be swapped
(OpenAI today, another vendor or a local model tomorrow) without touching
business logic. Every call returns token usage so cost can be tracked.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

Tier = Literal["fast", "smart"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    audio_seconds: float = 0.0


@dataclass
class ChatResult:
    text: str = ""
    parsed: BaseModel | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass
class ModerationResult:
    flagged: bool
    categories: list[str] = field(default_factory=list)


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def model_for(self, tier: Tier) -> str: ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: Tier,
        purpose: str,
        response_model: type[BaseModel] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage, str]: ...

    @abstractmethod
    async def transcribe(self, audio: bytes, filename: str) -> tuple[str, Usage, str]: ...

    @abstractmethod
    async def speak(self, text: str) -> tuple[bytes, Usage, str]: ...

    @abstractmethod
    async def moderate(self, text: str) -> ModerationResult: ...
