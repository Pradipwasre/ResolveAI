"""OpenAI implementation of the LLM interface."""

import io
import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import Settings
from app.llm.base import ChatResult, LLMProvider, ModerationResult, Tier, ToolCall, Usage


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=3,  # exponential backoff on 429 and 5xx
        )

    def model_for(self, tier: Tier) -> str:
        return self.settings.llm_model_smart if tier == "smart" else self.settings.llm_model_fast

    @staticmethod
    def _usage(raw: Any) -> Usage:
        if raw is None:
            return Usage()
        details = getattr(raw, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        return Usage(
            input_tokens=raw.prompt_tokens or 0,
            output_tokens=raw.completion_tokens or 0,
            cached_tokens=cached,
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
        model = self.model_for(tier)
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        # A stable key per purpose lets OpenAI reuse cached prompt prefixes (cheaper input tokens).
        kwargs["prompt_cache_key"] = f"resolveai-{purpose}"
        if response_model is not None:
            completion = await self.client.chat.completions.parse(response_format=response_model, **kwargs)
            message = completion.choices[0].message
            return ChatResult(
                text=message.content or "",
                parsed=message.parsed,
                model=model,
                usage=self._usage(completion.usage),
            )
        if tools:
            kwargs["tools"] = tools
        completion = await self.client.chat.completions.create(**kwargs)
        message = completion.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=json.loads(c.function.arguments or "{}"))
            for c in (message.tool_calls or [])
            if c.type == "function"
        ]
        return ChatResult(
            text=message.content or "",
            tool_calls=calls,
            model=model,
            usage=self._usage(completion.usage),
        )

    async def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage, str]:
        model = self.settings.embedding_model
        response = await self.client.embeddings.create(model=model, input=texts)
        vectors = [item.embedding for item in response.data]
        return vectors, Usage(input_tokens=response.usage.prompt_tokens), model

    async def transcribe(self, audio: bytes, filename: str) -> tuple[str, Usage, str]:
        model = self.settings.transcribe_model
        buffer = io.BytesIO(audio)
        buffer.name = filename
        result = await self.client.audio.transcriptions.create(model=model, file=buffer)
        # Telegram tells us the duration; the caller fills audio_seconds.
        return result.text, Usage(), model

    async def speak(self, text: str) -> tuple[bytes, Usage, str]:
        model = self.settings.tts_model
        response = await self.client.audio.speech.create(
            model=model,
            voice=self.settings.tts_voice,
            input=text[:4000],
            instructions="Speak warmly and clearly, like a helpful customer support agent.",
            response_format="opus",  # Telegram voice notes are OGG/Opus
        )
        audio = await response.aread()
        # TTS is billed per input text token; approximate as characters / 4.
        return audio, Usage(input_tokens=max(len(text) // 4, 1)), model

    async def moderate(self, text: str) -> ModerationResult:
        result = await self.client.moderations.create(model="omni-moderation-latest", input=text)
        first = result.results[0]
        categories = [name for name, hit in first.categories.model_dump().items() if hit]
        return ModerationResult(flagged=first.flagged, categories=categories)
