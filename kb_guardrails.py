from __future__ import annotations

import re

DISALLOWED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(ignore the source|make up|invent|hallucinate)\b",
        r"\b(delete|destroy|erase)\b",
        r"\b(secret|password|token|api key)\b",
    ]
]


def validate_instruction(instruction: str) -> tuple[bool, str | None]:
    value = instruction.strip()
    if not value:
        return False, "Enter a KB instruction before generating output."
    for pattern in DISALLOWED_PATTERNS:
        if pattern.search(value):
            return False, "Instruction violates guardrails. Keep the request grounded in the uploaded PDF."
    return True, None
