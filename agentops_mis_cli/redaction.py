"""Shared redaction helpers for AgentOps MIS CLI, worker, and server."""

from __future__ import annotations

import re


REDACTION_RULES: tuple[tuple[str, str], ...] = (
    (r"(?i)(authorization\s*:\s*bearer\s+)[a-z0-9._~+/\-=]+", r"\1[REDACTED]"),
    (r"(?i)(bearer\s+)[a-z0-9._~+/\-=]+", r"\1[REDACTED]"),
    (r"(?i)([\"']?(?:token|secret|password|api[_-]?key)[\"']?\s*:\s*[\"'])[^\"']+([\"'])", r"\1[REDACTED]\2"),
    (r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+", r"\1=[REDACTED]"),
    (r"(?i)\b(?:sk-[a-z0-9._~+/\-=]+|ntn_[a-z0-9._~+/\-=]+)\b", "[SECRET_REDACTED]"),
    (r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b", "[SECRET_REDACTED]"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[SECRET_REDACTED]"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "[SECRET_REDACTED]"),
    (r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[SECRET_REDACTED]"),
    (r"\bAIza[0-9A-Za-z_-]{30,}\b", "[SECRET_REDACTED]"),
    (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[SECRET_REDACTED]"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "[PRIVATE_KEY_REDACTED]"),
    (r"\b(?:agtok|agtsess|agthost)_[A-Za-z0-9_-]+\b", "[AGENT_TOKEN_REF_REDACTED]"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL_REDACTED]"),
    (r"(?<![\w])(?:\+\d{1,3}[\s.-]*)?(?:\(?\d{2,4}\)?[\s.-]+){2,4}\d{2,4}(?![\w])", "[PHONE_REDACTED]"),
)


def redact_full_text(text: str | None) -> str:
    value = str(text or "")
    for pattern, replacement in REDACTION_RULES:
        value = re.sub(pattern, replacement, value)
    return value


def redact_text(text: str | None, limit: int = 200) -> str:
    value = redact_full_text(text)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]
