"""Prompt-injection detection for customer messages and retrieved text.

A fast pattern check runs on every message. It is deliberately strict on
phrases that try to change the assistant's instructions, reveal hidden
prompts, or impersonate the system. Retrieved documents are also wrapped
in clear delimiters in the prompt, so the model treats them as data.
"""

import re

PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any |the )?(previous|prior|above|your) (instructions|rules)",
    r"forget (all |your )?(instructions|rules|guidelines)",
    r"(reveal|show|print|repeat|leak) (me )?(your|the) (system )?(prompt|instructions)",
    r"you are now (?!talking)",
    r"act as (an? )?(admin|developer|system|dan|jailbroken)",
    r"developer mode",
    r"\bjailbreak\b",
    r"^\s*system\s*:",
    r"<\s*/?\s*(system|instructions?)\s*>",
    r"approve (my |the |this )?refund (automatically|without)",
    r"pichle (sabhi )?nirdesh (bhool|ignore)",
    r"पिछले (सभी )?निर्देश",
]
_COMPILED = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in PATTERNS]


def detect_injection(text: str) -> str | None:
    """Return the matched pattern if the text looks like a prompt-injection attempt."""
    for pattern in _COMPILED:
        if pattern.search(text):
            return pattern.pattern
    return None
