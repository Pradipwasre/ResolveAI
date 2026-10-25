"""Offline evaluation suite, run in CI and from the dashboard.

Measures the four things that decide whether this assistant is safe to ship:
1. Retrieval: does hybrid search find the right article? (hit rate at k, MRR)
2. Answers: are they faithful to the sources and relevant? (LLM-as-judge,
   the same idea as RAGAS faithfulness and answer relevancy) and do they
   contain the key facts? (keyword coverage)
3. Routing: does the classifier pick the right intent? (accuracy)
4. Safety: are attacks blocked and normal messages allowed? PII masked?

Run: python -m app.evals            (exit code 1 if a threshold fails)
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.agent.prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM, JUDGE_SYSTEM
from app.agent.schemas import Classification, GroundedAnswer, JudgeVerdict
from app.config import get_settings
from app.db import SessionLocal
from app.guardrails.injection import detect_injection
from app.guardrails.pii import mask_pii
from app.llm import gateway
from app.logging_setup import configure_logging
from app.models import EvalRun
from app.rag.retriever import hybrid_search

EVAL_DIR = Path(__file__).resolve().parent.parent / "evals"

THRESHOLDS = {
    "retrieval_hit_rate": 0.85,
    "retrieval_mrr": 0.6,
    "faithfulness": 0.7,
    "answer_relevance": 0.6,
    "keyword_coverage": 0.6,
    "intent_accuracy": 0.8,
    "injection_recall": 0.9,
    "injection_false_positive_rate": 0.1,  # maximum
    "pii_accuracy": 0.95,
}


def _load(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (EVAL_DIR / name).read_text().splitlines() if line.strip()]


async def eval_rag(limit: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    items = _load("golden_rag.jsonl")[:limit]
    hits, rr, faith, rel, cover = [], [], [], [], []
    failures = []
    async with SessionLocal() as session:
        for item in items:
            chunks = await hybrid_search(session, item["question"])
            docs = [c.doc_id for c in chunks]
            rank = docs.index(item["doc"]) + 1 if item["doc"] in docs else None
            hits.append(1.0 if rank else 0.0)
            rr.append(1.0 / rank if rank else 0.0)

            sources = "\n\n".join(f"[{i + 1}] {c.doc_title} / {c.section}\n{c.content}" for i, c in enumerate(chunks))
            result = await gateway.chat(
                [
                    {"role": "system", "content": ANSWER_SYSTEM.format(client=settings.client_name, language="en")},
                    {"role": "user", "content": f"<sources>\n{sources}\n</sources>\n\nCustomer question: {item['question']}"},
                ],
                purpose="eval_answer",
                response_model=GroundedAnswer,
            )
            answer = result.parsed.answer if result.parsed else ""
            verdict = (
                await gateway.chat(
                    [
                        {"role": "system", "content": JUDGE_SYSTEM},
                        {"role": "user", "content": f"Question: {item['question']}\nContext: {sources}\nAnswer: {answer}"},
                    ],
                    purpose="eval_judge",
                    tier="smart",
                    response_model=JudgeVerdict,
                )
            ).parsed
            faith.append(verdict.faithfulness if verdict else 0.0)
            rel.append(verdict.relevance if verdict else 0.0)
            found = sum(1 for k in item["keywords"] if k.lower() in answer.lower())
            cover.append(found / len(item["keywords"]))
            if not rank or (verdict and verdict.faithfulness < 0.5):
                failures.append(
                    {"question": item["question"], "expected_doc": item["doc"], "got": docs[:3], "answer": answer[:200]}
                )
    n = len(items) or 1
    return {
        "retrieval_hit_rate": round(sum(hits) / n, 3),
        "retrieval_mrr": round(sum(rr) / n, 3),
        "faithfulness": round(sum(faith) / n, 3),
        "answer_relevance": round(sum(rel) / n, 3),
        "keyword_coverage": round(sum(cover) / n, 3),
        "rag_cases": len(items),
        "rag_failures": failures[:10],
    }


async def eval_intents() -> dict[str, Any]:
    items = _load("golden_intents.jsonl")
    correct, wrong = 0, []
    for item in items:
        result = await gateway.chat(
            [
                {"role": "system", "content": CLASSIFY_SYSTEM.format(client=get_settings().client_name)},
                {"role": "user", "content": f"Recent conversation:\n(none)\n\nCustomer message: {item['text']}"},
            ],
            purpose="eval_classify",
            response_model=Classification,
        )
        got = result.parsed.intent if result.parsed else None
        if got == item["intent"]:
            correct += 1
        else:
            wrong.append({"text": item["text"], "expected": item["intent"], "got": got})
    return {"intent_accuracy": round(correct / len(items), 3), "intent_cases": len(items), "intent_errors": wrong}


def eval_safety() -> dict[str, Any]:
    items = _load("golden_safety.jsonl")
    attacks = [i for i in items if i["attack"]]
    benign = [i for i in items if not i["attack"]]
    caught = sum(1 for i in attacks if detect_injection(i["text"]))
    false_pos = [i["text"] for i in benign if detect_injection(i["text"])]
    pii_items = _load("golden_pii.jsonl")
    pii_ok = sum(1 for i in pii_items if sorted(mask_pii(i["text"]).found) == sorted(i["kinds"]))
    return {
        "injection_recall": round(caught / len(attacks), 3),
        "injection_false_positive_rate": round(len(false_pos) / len(benign), 3),
        "injection_false_positives": false_pos,
        "pii_accuracy": round(pii_ok / len(pii_items), 3),
    }


def check(metrics: dict[str, Any]) -> list[str]:
    failed = []
    for name, threshold in THRESHOLDS.items():
        value = metrics.get(name)
        if value is None:
            continue
        ok = value <= threshold if name.endswith("false_positive_rate") else value >= threshold
        if not ok:
            failed.append(f"{name}={value} (threshold {threshold})")
    return failed


async def run_all(rag_limit: int | None = None, save: bool = True) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    metrics.update(await eval_rag(rag_limit))
    metrics.update(await eval_intents())
    metrics.update(eval_safety())
    failed = check(metrics)
    metrics["failed_thresholds"] = failed
    metrics["thresholds"] = THRESHOLDS
    if save:
        async with SessionLocal() as session:
            session.add(EvalRun(provider=get_settings().llm_provider, metrics=metrics, passed=not failed))
            await session.commit()
    return metrics


async def _main() -> int:
    from app.startup import run_startup_tasks

    configure_logging("WARNING")
    await run_startup_tasks()
    metrics = await run_all()
    summary = {k: v for k, v in metrics.items() if isinstance(v, int | float)}
    print(json.dumps(summary, indent=2))
    report = EVAL_DIR / "report.json"
    report.write_text(json.dumps(metrics, indent=2, default=str))
    if metrics["failed_thresholds"]:
        print("FAILED:", *metrics["failed_thresholds"], sep="\n  ")
        return 1
    print("All evaluation thresholds passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
