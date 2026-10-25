"""Find and mask personal data before it reaches the LLM, logs or the database.

Values the agent legitimately needs (an email to verify the customer, an
order id) are extracted first and passed to the agent separately, so the
model never sees raw personal data.
"""

import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
AADHAAR = re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PHONE = re.compile(r"(?<![\w-])(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}\b")
OTP_OR_PIN = re.compile(r"\b(?:otp|pin|cvv|password)\s*(?:is|:)?\s*\S+", re.IGNORECASE)


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class PIIResult:
    masked_text: str
    found: list[str] = field(default_factory=list)  # kinds found, e.g. ["email", "card"]
    emails: list[str] = field(default_factory=list)


def mask_pii(text: str) -> PIIResult:
    found: list[str] = []
    emails = EMAIL.findall(text)

    def sub(pattern: re.Pattern[str], label: str, value: str, check=None) -> str:
        def repl(match: re.Match[str]) -> str:
            if check and not check(match.group(0)):
                return match.group(0)
            found.append(label)
            return f"[{label.upper()}]"

        return pattern.sub(repl, value)

    masked = sub(OTP_OR_PIN, "secret", text)
    masked = sub(CARD, "card", masked, _luhn_ok)
    masked = sub(AADHAAR, "aadhaar", masked)
    masked = sub(PAN, "pan", masked)
    masked = sub(PHONE, "phone", masked)
    masked = sub(EMAIL, "email", masked)
    return PIIResult(masked_text=masked, found=sorted(set(found)), emails=emails)
