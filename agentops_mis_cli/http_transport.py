"""Fail-closed HTTP transport helpers for credentialed AgentOps clients."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from agentops_mis_cli.redaction import redact_text


class NoCredentialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credential_transport_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        return False
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def credential_opener():
    return build_opener(ProxyHandler({}), NoCredentialRedirects())


def safe_credential_error(value: object, credential: str, limit: int) -> str:
    raw = str(value or "")
    if credential:
        raw = raw.replace(credential, "[REDACTED]")
    return redact_text(raw, limit)
