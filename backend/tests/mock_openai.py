"""A tiny stand-in for the OpenAI HTTP API, so the real OpenAI provider code
(request building, structured output parsing, tool calls, audio) is tested
without a key or network access."""

import json

from fastapi import FastAPI, Request, Response

app = FastAPI()
calls: list[dict] = []

STRUCTURED = {
    "Classification": {
        "intent": "knowledge",
        "language": "en",
        "sentiment": "neutral",
        "order_id": None,
        "subject": "Refund timeline",
    },
    "GroundedAnswer": {
        "answer": "UPI refunds reach you within 1 to 3 business days [1].",
        "cited_sources": [1],
        "confidence": 0.9,
    },
    "JudgeVerdict": {"faithfulness": 1.0, "relevance": 0.9, "reasoning": "ok"},
    "RerankScores": {"scores": [0.9, 0.1]},
}


def _completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5.4-mini",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop", "logprobs": None}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 64},
        },
    }


@app.post("/v1/chat/completions")
async def chat(request: Request) -> dict:
    body = await request.json()
    calls.append(body)
    fmt = body.get("response_format")
    if fmt and fmt.get("type") == "json_schema":
        name = fmt["json_schema"]["name"]
        return _completion({"role": "assistant", "content": json.dumps(STRUCTURED[name]), "refusal": None})
    if body.get("tools"):
        if not any(m["role"] == "tool" for m in body["messages"]):
            call = {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_order", "arguments": '{"order_id": "UC-10001"}'},
            }
            return _completion({"role": "assistant", "content": None, "tool_calls": [call]})
        return _completion({"role": "assistant", "content": "Your order UC-10001 is shipped with Delhivery."})
    return _completion({"role": "assistant", "content": "Summary of the conversation."})


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> dict:
    body = await request.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": [0.01] * 1536} for i in range(len(inputs))],
        "model": body["model"],
        "usage": {"prompt_tokens": 10 * len(inputs), "total_tokens": 10 * len(inputs)},
    }


@app.post("/v1/audio/transcriptions")
async def transcribe() -> dict:
    return {"text": "Where is my order UC-10001?"}


@app.post("/v1/audio/speech")
async def speech() -> Response:
    return Response(content=b"OggS-fake-opus", media_type="audio/ogg")


@app.post("/v1/moderations")
async def moderations(request: Request) -> dict:
    body = await request.json()
    flagged = "kill" in str(body["input"])
    categories = {"violence": flagged, "harassment": False}
    return {
        "id": "modr-1",
        "model": "omni-moderation-latest",
        "results": [
            {
                "flagged": flagged,
                "categories": categories,
                "category_scores": {k: float(v) for k, v in categories.items()},
                "category_applied_input_types": {k: ["text"] for k in categories},
            }
        ],
    }
