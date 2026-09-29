from __future__ import annotations

import hashlib
import re

# Thứ tự quan trọng: dãy số dài/đặc thù (thẻ, CCCD) phải được che trước số điện thoại.
PII_PATTERNS: dict[str, str] = {
    "credit_card": r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)",
    "cccd": r"(?<!\d)\d{12}(?!\d)",
    "phone_vn": r"(?<!\d)(?:\+84|84|0)(?:[ .-]?\d){9}(?!\d)",
    "email": r"[\w\.-]+@[\w\.-]+\.\w+",
    "passport": r"\b[A-Z]\d{7}\b",
    "address": r"(?i)\b(?:địa chỉ|dia chi|address)\s*[:\-]\s*[^\n,;]{5,80}",
}


def scrub_text(text: str) -> str:
    safe = text
    for name, pattern in PII_PATTERNS.items():
        safe = re.sub(pattern, f"[REDACTED_{name.upper()}]", safe)
    return safe


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
