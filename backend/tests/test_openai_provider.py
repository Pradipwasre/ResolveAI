"""Runs the real OpenAIProvider against a local mock of the OpenAI API."""

import socket
import threading
import time

import pytest
import uvicorn

from app.agent.schemas import Classification, GroundedAnswer
from app.agent.tools import TOOL_SCHEMAS
from app.config import Settings
from app.llm.openai_provider import OpenAIProvider
from tests import mock_openai


@pytest.fixture(scope="module")
def provider():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(mock_openai.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    settings = Settings(llm_provider="openai", openai_api_key="test-key")
    p = OpenAIProvider(settings)
    p.client = p.client.with_options(base_url=f"http://127.0.0.1:{port}/v1")
    yield p
    server.should_exit = True


async def test_structured_output(provider):
    result = await provider.chat(
        [{"role": "user", "content": "hi"}], tier="fast", purpose="classify", response_model=Classification
    )
    assert isinstance(result.parsed, Classification)
    assert result.usage.input_tokens == 120 and result.usage.cached_tokens == 64
    sent = mock_openai.calls[-1]
    assert sent["model"] == "gpt-5.4-mini" and sent["prompt_cache_key"] == "resolveai-classify"

    answer = await provider.chat(
        [{"role": "user", "content": "q"}], tier="smart", purpose="answer", response_model=GroundedAnswer
    )
    assert answer.parsed.cited_sources == [1]
    assert mock_openai.calls[-1]["model"] == "gpt-5.4"


async def test_tool_calls(provider):
    result = await provider.chat(
        [{"role": "user", "content": "where is UC-10001"}], tier="fast", purpose="account", tools=TOOL_SCHEMAS
    )
    assert result.tool_calls[0].name == "get_order"
    assert result.tool_calls[0].arguments == {"order_id": "UC-10001"}


async def test_embeddings_audio_and_moderation(provider):
    vectors, usage, model = await provider.embed(["a", "b"])
    assert len(vectors) == 2 and len(vectors[0]) == 1536 and usage.input_tokens == 20
    text, _, _ = await provider.transcribe(b"audio", "voice.ogg")
    assert "UC-10001" in text
    audio, _, _ = await provider.speak("hello")
    assert audio.startswith(b"OggS")
    assert (await provider.moderate("I will kill you")).flagged
    assert not (await provider.moderate("where is my order")).flagged
