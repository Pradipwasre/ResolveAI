"""Checks on every AI reply before it is sent to the customer."""

import re
from dataclasses import dataclass, field

from app.guardrails.pii import mask_pii
from app.llm.fake_provider import overlap

MAX_REPLY_CHARS = 1500
FORBIDDEN = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"system prompt",
        r"as an ai language model",
        r"guaranteed? (refund|delivery) (today|now)",
        r"share (your )?(otp|cvv|password|pin)",
    ]
]


@dataclass
class OutputCheck:
    text: str
    passed: bool = True
    issues: list[str] = field(default_factory=list)


def check_output(
    text: str, *, sources: list[str] | None = None, allowed_emails: list[str] | None = None, min_grounding: float = 0.25
) -> OutputCheck:
    issues: list[str] = []
    result = mask_pii(text)
    cleaned = text
    leaked = [e for e in result.emails if e not in (allowed_emails or [])]
    if leaked or set(result.found) - {"email"}:
        cleaned = result.masked_text
        issues.append("pii_in_output")
    for pattern in FORBIDDEN:
        if pattern.search(cleaned):
            issues.append("forbidden_phrase")
    if len(cleaned) > MAX_REPLY_CHARS:
        cleaned = cleaned[:MAX_REPLY_CHARS].rsplit(" ", 1)[0] + "..."
        issues.append("too_long")
    if sources is not None:
        if not re.search(r"\[\d+\]", cleaned):
            issues.append("no_citation")
        support = overlap(re.sub(r"\[\d+\]", "", cleaned), " ".join(sources))
        if support < min_grounding:
            issues.append("low_grounding")
    blocking = {"forbidden_phrase", "no_citation", "low_grounding"}
    return OutputCheck(text=cleaned, passed=not (set(issues) & blocking), issues=issues)
