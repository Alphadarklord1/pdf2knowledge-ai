from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(\+?\d[\d\s\-]{6,}\d)")
LONG_ID_RE = re.compile(r"\b\d{8,14}\b")


def mask_sensitive_text(value: str, enabled: bool = True) -> str:
    if not enabled or not value:
        return value
    masked = EMAIL_RE.sub("[masked-email]", value)
    masked = PHONE_RE.sub("[masked-phone]", masked)
    masked = LONG_ID_RE.sub("[masked-id]", masked)
    return masked
