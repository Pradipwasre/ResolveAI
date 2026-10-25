"""Model prices in USD, used to compute the cost of every call.

Prices change. Check https://openai.com/api/pricing and update this table
(or override a model with the MODEL_PRICES_JSON environment variable).
Values: per 1M input tokens, per 1M cached input tokens, per 1M output tokens.
Audio models are priced per minute of audio.
"""

import json
import os

TOKEN_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.4": (1.25, 0.125, 10.00),
    "gpt-5.4-mini": (0.25, 0.025, 2.00),
    "gpt-5.4-nano": (0.05, 0.005, 0.40),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.13, 0.0),
    "gpt-4o-mini-tts": (0.60, 0.60, 12.00),
    "fake-fast": (0.25, 0.025, 2.00),
    "fake-smart": (1.25, 0.125, 10.00),
    "fake-embed": (0.02, 0.02, 0.0),
    "fake-tts": (0.60, 0.60, 12.00),
}

AUDIO_PRICE_PER_MINUTE: dict[str, float] = {
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
    "fake-transcribe": 0.003,
}

_override = os.getenv("MODEL_PRICES_JSON")
if _override:
    for name, values in json.loads(_override).items():
        TOKEN_PRICES[name] = tuple(values)  # type: ignore[assignment]


def token_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    price_in, price_cached, price_out = TOKEN_PRICES.get(model, TOKEN_PRICES["gpt-5.4-mini"])
    uncached = max(input_tokens - cached_tokens, 0)
    return (uncached * price_in + cached_tokens * price_cached + output_tokens * price_out) / 1_000_000


def audio_cost(model: str, seconds: float) -> float:
    return AUDIO_PRICE_PER_MINUTE.get(model, 0.006) * seconds / 60
