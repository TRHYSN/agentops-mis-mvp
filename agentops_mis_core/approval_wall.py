"""Pure Approval Wall prepared-action helpers."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import datetime as dt
from typing import Any
from urllib.parse import urlparse


REDACTION_RULES: tuple[tuple[str, str], ...] = (
    (r"(?i)(authorization\s*:\s*bearer\s+)[a-z0-9._~+/\-=]+", r"\1[REDACTED]"),
    (r"(?i)(bearer\s+)[a-z0-9._~+/\-=]+", r"\1[REDACTED]"),
    (r"(?i)([\"']?(?:token|secret|password|api[_-]?key)[\"']?\s*:\s*[\"'])[^\"']+([\"'])", r"\1[REDACTED]\2"),
    (r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+", r"\1=[REDACTED]"),
    (r"(?i)\b(?:sk-[a-z0-9._~+/\-=]+|ntn_[a-z0-9._~+/\-=]+)\b", "[SECRET_REDACTED]"),
    (r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b", "[SECRET_REDACTED]"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "[SECRET_REDACTED]"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "[SECRET_REDACTED]"),
    (r"\b(?:agtok|agtsess)_[A-Za-z0-9_-]+\b", "[AGENT_TOKEN_REF_REDACTED]"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL_REDACTED]"),
    (r"(?<![\w])(?:\+\d{1,3}[\s.-]*)?(?:\(?\d{2,4}\)?[\s.-]+){2,4}\d{2,4}(?![\w])", "[PHONE_REDACTED]"),
)

RISKY_TOOLS = {
    "shell.exec",
    "github.push",
    "email.send",
    "file.delete",
    "database.write",
    "dify.knowledge.upload",
    "openai.file_search.upload",
}
EXTERNAL_SIDE_EFFECT_KEYWORDS = {
    "external",
    "publish",
    "upload",
    "write",
    "send",
    "post",
    "push",
    "export",
    "deliver",
    "file_search",
    "knowledge",
}
EXTERNAL_SIDE_EFFECT_SCHEMES = (
    "http://",
    "https://",
    "openai://",
    "dify://",
    "notion://",
    "github://",
    "slack://",
    "discord://",
    "email://",
)
EXTERNAL_WRITE_INTENT_KEYWORDS = (
    "knowledge base upload",
    "customer portal",
    "external write",
    "send email",
    "file search",
    "生产发布",
    "知识库上传",
    "外部写入",
    "publish",
    "upload",
    "deploy",
    "push",
    "send",
    "webhook",
    "notion",
    "dify",
    "dataset",
    "发布",
    "上传",
    "部署",
    "推送",
    "发邮件",
    "发送",
)
EXTERNAL_WRITE_NEGATION_RE = re.compile(
    r"(?:"
    r"\b(?:do(?:es)?\s+not|don't|must\s+not|shall\s+not|may\s+not|should\s+not|"
    r"will\s+not|would\s+not|could\s+not|never|cannot|can't)\b[^.!?;\n]{0,64}|"
    r"(?:不要|不得|禁止|不允许|不再|不能|不会)[^。！？；\n]{0,40}"
    r")$",
    re.IGNORECASE,
)
EXTERNAL_WRITE_ACTION_CHAIN_TERM = (
    r"(?:publish(?:es|ed|ing)?|upload(?:s|ed|ing)?|deploy(?:s|ed|ing)?|"
    r"push(?:es|ed|ing)?|send(?:s|ing)?|sent|external\s+writ(?:e|es|ing|ten)|"
    r"生产发布|知识库上传|外部写入|发布|上传|部署|推送|发邮件|发送)"
)
EXTERNAL_WRITE_TIGHT_NEGATION_RE = re.compile(
    r"(?:"
    r"\b(?:without|no)\b(?:\s+(?:any|an|a|direct|actual|external|production|unauthorized|outbound))*|"
    r"(?:无需|无须|不进行|不执行|不使用|不调用)(?:任何形式的|任何|外部|对外|直接|实际)*|"
    r"不(?!仅|但)(?:再|向外部|对外|直接|实际)?"
    r")\s*"
    rf"(?:{EXTERNAL_WRITE_ACTION_CHAIN_TERM}\s*(?:,|、|，)?\s*(?:or|and|或|和|及|与)\s*)*$",
    re.IGNORECASE,
)
EXTERNAL_WRITE_NEGATION_BREAK_RE = re.compile(
    r"(?:\b(?:but|however|then|instead|except)\b|但是|但|不过|然而|然后|随后|(?<!不)再|改为|而是|除外)",
    re.IGNORECASE,
)
EXTERNAL_WRITE_ASCII_KEYWORD_PATTERNS = {
    "publish": r"publish(?:es|ed|ing)?",
    "upload": r"upload(?:s|ed|ing)?",
    "deploy": r"deploy(?:s|ed|ing)?",
    "push": r"push(?:es|ed|ing)?",
    "send": r"(?:send(?:s|ing)?|sent)",
    "webhook": r"webhooks?",
    "dataset": r"datasets?",
}


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_workspace_id(value: Any) -> str:
    raw = str(value or "local-demo").strip()[:120]
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw).strip("_")
    return normalized or "local-demo"


def redact_text(text: Any, limit: int = 200) -> str:
    value = str(text or "")
    for pattern, replacement in REDACTION_RULES:
        value = re.sub(pattern, replacement, value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def external_write_keyword_indexes(text: str, keyword: str):
    if keyword.isascii():
        body = EXTERNAL_WRITE_ASCII_KEYWORD_PATTERNS.get(keyword, re.escape(keyword))
        pattern = re.compile(rf"(?<![a-z0-9_])(?:{body})(?![a-z0-9_])", re.IGNORECASE)
        yield from (match.start() for match in pattern.finditer(text))
        return

    start = 0
    while True:
        index = text.find(keyword, start)
        if index < 0:
            return
        yield index
        start = index + len(keyword)


def positive_external_write_intent(text: Any) -> bool:
    """Detect write intent while ignoring only explicitly negated occurrences."""
    lowered = str(text or "").lower()
    for keyword in EXTERNAL_WRITE_INTENT_KEYWORDS:
        needle = keyword.lower()
        for index in external_write_keyword_indexes(lowered, needle):
            prefix = lowered[max(0, index - 80):index]
            negation = EXTERNAL_WRITE_NEGATION_RE.search(prefix) or EXTERNAL_WRITE_TIGHT_NEGATION_RE.search(prefix)
            if negation is None or EXTERNAL_WRITE_NEGATION_BREAK_RE.search(negation.group(0)):
                return True
    return False


def is_loopback_http_target(value: Any) -> bool:
    """Accept only an exact HTTP loopback hostname, never a string prefix."""
    try:
        parsed = urlparse(str(value or "").strip())
        hostname = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if parsed.scheme.lower() != "http" or not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def task_has_external_write_intent(
    *,
    title: Any,
    description: Any,
    acceptance_criteria: Any,
    target_resource: Any = None,
    external_action_type: Any = None,
    explicit_intent: Any = None,
) -> bool:
    """Classify task intent without allowing negative prose to bypass real writes."""
    if explicit_intent is not None:
        normalized_intent = str(explicit_intent).strip().lower()
        if explicit_intent is not False and normalized_intent not in {"", "0", "false", "no", "off"}:
            return True

    action_type = str(external_action_type or "").strip()
    if action_type:
        return True

    target = str(target_resource or "").strip().lower()
    target_is_loopback = is_loopback_http_target(target)
    if target.startswith(EXTERNAL_SIDE_EFFECT_SCHEMES) and not target_is_loopback:
        return True
    if positive_external_write_intent(target):
        return True

    return any(
        positive_external_write_intent(value)
        for value in (title, description, acceptance_criteria)
    )


def safe_json_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key)[:80]: safe_json_metadata(item) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return [safe_json_metadata(item) for item in value[:40]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(value, 240)


def _parse_json_metadata(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value or "{}")
        except Exception:
            return value
    return value


def prepared_action_id_from_request(body: dict[str, Any]) -> Any:
    return body.get("prepared_action_id") or body.get("action_id")


def prepared_action_stored_args(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    value = _parse_json_metadata(data.get("normalized_args_json") or "{}")
    return value if isinstance(value, dict) else {}


def prepared_action_checkpoint(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    value = _parse_json_metadata(data.get("checkpoint_json") or "{}")
    return value if isinstance(value, dict) else {}


def prepared_action_hash_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": normalize_workspace_id(row.get("workspace_id")),
        "task_id": row.get("task_id"),
        "run_id": row.get("run_id"),
        "tool_call_id": row.get("tool_call_id"),
        "requested_by_agent_id": row.get("requested_by_agent_id"),
        "action_type": row.get("action_type"),
        "normalized_args_json": row.get("normalized_args_json") or "{}",
        "target_resource": row.get("target_resource"),
        "risk_level": row.get("risk_level"),
        "policy_version": row.get("policy_version") or "approval-wall-v1",
        "checkpoint_json": row.get("checkpoint_json") or "{}",
        "idempotency_key": row.get("idempotency_key"),
        "expires_at": row.get("expires_at"),
    }


def prepared_action_hash(row: dict[str, Any]) -> str:
    return stable_hash(prepared_action_hash_payload(row))


def prepared_action_hash_verification(row: dict[str, Any]) -> dict[str, Any]:
    current_hash = prepared_action_hash(row)
    return {
        "stored_action_hash": row.get("action_hash"),
        "current_action_hash": current_hash,
        "match": current_hash == row.get("action_hash"),
    }


def prepared_action_public(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    normalized_args = _parse_json_metadata(data.get("normalized_args_json") or "{}")
    checkpoint = _parse_json_metadata(data.get("checkpoint_json") or "{}")
    data["normalized_args"] = safe_json_metadata(normalized_args)
    data["checkpoint"] = safe_json_metadata(checkpoint)
    data["normalized_args_json"] = json.dumps(data["normalized_args"], ensure_ascii=False, sort_keys=True)
    data["checkpoint_json"] = json.dumps(data["checkpoint"], ensure_ascii=False, sort_keys=True)
    data["raw_prompt_omitted"] = True
    data["raw_response_omitted"] = True
    data["token_omitted"] = True
    return data


def prepared_action_gate(row: Any) -> dict[str, Any]:
    if not row:
        return {
            "required_for_exact_resume": False,
            "action_hash": None,
            "hash_match": None,
            "status": None,
            "consumed_at": None,
        }
    data = dict(row)
    verification = prepared_action_hash_verification(data)
    return {
        "required_for_exact_resume": True,
        "action_hash": data.get("action_hash"),
        "hash_match": verification["match"],
        "status": data.get("status"),
        "consumed_at": data.get("consumed_at"),
    }


def prepared_action_resume_gate_error(
    *,
    action_id: Any,
    row: Any,
    approval: Any,
    expected_args: dict[str, Any],
    expected_action_type: str,
    comparable_fields: tuple[str, ...],
    missing_error: str,
    missing_message: str,
    approval_message: str,
    extra_mismatches: list[str] | None = None,
) -> dict[str, Any] | None:
    if not action_id:
        return {"error": missing_error, "message": missing_message, "token_omitted": True}
    if not row:
        return {"error": "prepared_action_not_found", "prepared_action_id": action_id, "token_omitted": True}

    data = dict(row)
    approval_data = dict(approval) if approval else None
    if not approval_data or approval_data.get("decision") != "approved":
        return {
            "error": "approval_required",
            "message": approval_message,
            "approval_id": data.get("approval_id"),
            "prepared_action_id": action_id,
            "decision": approval_data.get("decision") if approval_data else None,
            "token_omitted": True,
        }
    expires_at = data.get("expires_at") or approval_data.get("expires_at")
    if expires_at:
        try:
            parsed_expiry = dt.datetime.fromisoformat(str(expires_at))
            if parsed_expiry.tzinfo is None:
                parsed_expiry = parsed_expiry.replace(tzinfo=dt.timezone.utc)
            if parsed_expiry <= dt.datetime.now(dt.timezone.utc):
                return {
                    "error": "prepared_action_expired",
                    "prepared_action_id": action_id,
                    "approval_id": data.get("approval_id"),
                    "token_omitted": True,
                }
        except ValueError:
            return {
                "error": "prepared_action_expiry_invalid",
                "prepared_action_id": action_id,
                "token_omitted": True,
            }
    if data.get("consumed_at") or data.get("status") == "consumed":
        return {
            "error": "prepared_action_already_consumed",
            "prepared_action": prepared_action_public(data),
            "token_omitted": True,
        }

    verification = prepared_action_hash_verification(data)
    if not verification["match"]:
        return {
            "error": "action_hash_mismatch",
            "stored_action_hash": verification["stored_action_hash"],
            "current_action_hash": verification["current_action_hash"],
            "token_omitted": True,
        }

    stored_args = prepared_action_stored_args(data)
    mismatched = [
        field
        for field in comparable_fields
        if stored_args.get(field) != expected_args.get(field)
    ]
    mismatched.extend(item for item in (extra_mismatches or []) if item)
    if data.get("action_type") != expected_action_type or mismatched:
        return {
            "error": "prepared_action_request_mismatch",
            "prepared_action_id": action_id,
            "mismatched_fields": mismatched or ["action_type"],
            "token_omitted": True,
        }
    return None


def prepared_action_waiting_next_action(
    *,
    approval_id: Any,
    prepared_action_id: Any,
    resume_instruction: str,
) -> str:
    template_values = {
        "approval_id": approval_id,
        "prepared_action_id": prepared_action_id,
        "action_id": prepared_action_id,
    }
    try:
        formatted_resume = resume_instruction.format(**template_values)
    except Exception:
        formatted_resume = resume_instruction
    return (
        f"agentops approval inspect --approval-id {approval_id} && "
        f"agentops approval approve --approval-id {approval_id} && "
        f"{formatted_resume}"
    )


def build_prepared_action_waiting_response(
    *,
    base: dict[str, Any],
    approval_wall: dict[str, Any],
    reason: str,
    resume_instruction: str,
    include_prepared_action_hash: bool = True,
) -> dict[str, Any]:
    wall = dict(approval_wall or {})
    approval = wall.get("approval") or {}
    prepared_action = wall.get("prepared_action") or {}
    approval_id = approval.get("approval_id")
    prepared_action_id = prepared_action.get("action_id")
    response = {
        **base,
        "status": "waiting_approval",
        "reason": reason,
        "approval_wall": wall,
        "approval_id": approval_id,
        "prepared_action_id": prepared_action_id,
        "next_action": prepared_action_waiting_next_action(
            approval_id=approval_id,
            prepared_action_id=prepared_action_id,
            resume_instruction=resume_instruction,
        ),
        "token_omitted": True,
    }
    if include_prepared_action_hash:
        response["prepared_action_hash"] = prepared_action.get("action_hash")
    return response


def build_prepared_action_prepare_response_fields(prepared: Any) -> dict[str, Any]:
    payload = prepared if isinstance(prepared, dict) else {}
    approval = payload.get("approval") or {}
    prepared_action = payload.get("prepared_action") or {}
    return {
        "approval_wall": {
            "prepared_action": payload.get("prepared_action"),
            "approval": payload.get("approval"),
            "resume_contract": payload.get("resume_contract"),
            "operation": payload.get("operation"),
            "outcome": payload.get("outcome"),
            "token_omitted": True,
        },
        "next_action": prepared_action_waiting_next_action(
            approval_id=approval.get("approval_id"),
            prepared_action_id=prepared_action.get("action_id"),
            resume_instruction="agentops approval prepared-action resume --action-id {action_id} --provider-side-effect-id <id>",
        ),
    }


def build_prepared_action_approval_decision_response(
    *,
    approval: Any,
    prepared_action: Any,
    decision: str,
) -> dict[str, Any]:
    return {
        "approval": dict(approval) if approval is not None else None,
        "prepared_action": prepared_action_public(prepared_action),
        "resume_required": decision == "approved",
        "token_omitted": True,
    }


def build_high_risk_toolcall_prepared_action_required_response(
    *,
    tool_name: Any,
    risk_level: Any,
    requested_status: Any,
    external_side_effect_intent: Any,
    run_id: Any,
    task_id: Any,
) -> dict[str, Any]:
    return {
        "error": "high_risk_prepared_action_required",
        "message": "High-risk or critical external side-effect tool calls must use prepare_action=true and resume the prepared action after approval.",
        "tool_name": tool_name,
        "risk_level": risk_level,
        "requested_status": requested_status,
        "external_side_effect_intent": bool(external_side_effect_intent),
        "run_id": run_id,
        "task_id": task_id,
        "next_action": "Record again with prepare_action=true, inspect/approve the generated approval, then resume the prepared action exactly once with provider_side_effect_id.",
        "token_omitted": True,
    }


def tool_call_has_external_side_effect_intent(
    tool_name: str,
    category: str,
    target_resource: str | None,
    args: dict,
) -> bool:
    scanned_args = dict(args or {})
    # Capability metadata says whether a runtime would need a prepared action
    # for external writes; it is not itself an external write intent.
    safe_metadata_keys = {
        "attempt_count",
        "commercial_readiness",
        "requires_prepared_action_for_external_write",
        "credential_storage",
        "credential_storage_policy",
        "credentials_stored",
        "credential_transport",
        "effective_risk_level",
        "external_writes_supported",
        "max_attempts",
        "model_visible_credentials",
        "observation_level",
        "read_only_runtime",
        "agent_id",
        "knowledge_retrieval_evidence_consumed",
        "knowledge_context_approved_memory_ids",
        "knowledge_context_block_count",
        "knowledge_context_block_hashes",
        "knowledge_context_consumed",
        "knowledge_context_contract_version",
        "knowledge_context_packet_hash",
        "knowledge_retrieval_ids",
        "knowledge_retrieval_metrics",
        "knowledge_retrieval_omissions",
        "knowledge_retrieval_packet_hash",
        "knowledge_retrieval_paths",
        "knowledge_retrieval_query_hash",
        "knowledge_retrieval_source_hashes",
        "knowledge_retrieval_status",
        "knowledge_retrieval_task_context",
        "plan_id",
        "prompt_hash",
        "prompt_profile_hash",
        "prompt_profile_id",
        "prompt_profile_version",
        "raw_omitted",
        "raw_document_storage",
        "raw_documents_stored",
        "raw_payload_stored",
        "raw_text_omitted",
        "raw_prompt_omitted",
        "raw_response_omitted",
        "retry_history",
        "risk_floor",
        "runtime_events_structured",
        "run_id",
        "secret_boundary",
        "secrets_in_output",
        "secrets_in_prompt",
        "summary_only",
        "task_id",
        "token_omitted",
        "workspace_id",
    }
    for key in list(scanned_args):
        if key in safe_metadata_keys or str(key).endswith("_storage"):
            scanned_args.pop(key, None)
    explicit_target = str(
        scanned_args.get("target")
        or scanned_args.get("url")
        or scanned_args.get("endpoint")
        or scanned_args.get("resource")
        or scanned_args.get("destination")
        or ""
    ).strip().lower()
    haystack = " ".join([
        tool_name or "",
        category or "",
        target_resource or "",
        json.dumps(scanned_args, ensure_ascii=False, sort_keys=True),
    ]).lower()
    target = (target_resource or "").strip().lower()
    target_is_loopback = is_loopback_http_target(target)
    explicit_target_is_loopback = is_loopback_http_target(explicit_target)
    if (
        (target.startswith(EXTERNAL_SIDE_EFFECT_SCHEMES) and not target_is_loopback)
        or (explicit_target.startswith(EXTERNAL_SIDE_EFFECT_SCHEMES) and not explicit_target_is_loopback)
    ):
        return True
    if tool_name in RISKY_TOOLS:
        return True
    if category in {"email", "notion", "github", "discord", "mcp", "database"} and any(keyword in haystack for keyword in EXTERNAL_SIDE_EFFECT_KEYWORDS):
        return True
    return any(keyword in haystack for keyword in EXTERNAL_SIDE_EFFECT_KEYWORDS)


def build_prepared_action_blocked_response(
    *,
    base: dict[str, Any],
    gate_error: dict[str, Any],
    status: str = "blocked",
) -> dict[str, Any]:
    return {
        **base,
        **gate_error,
        "status": status,
        "reason": gate_error.get("error"),
        "token_omitted": True,
    }


def build_prepared_action_get_not_found_response(action_id: Any) -> dict[str, Any]:
    return {
        "error": "not_found",
        "message": f"prepared action {action_id} not found",
        "token_omitted": True,
    }


def build_prepared_action_agent_forbidden_response(*, operation: str) -> dict[str, Any]:
    verb = "resume" if operation == "resume" else "inspect"
    return {
        "error": "forbidden",
        "message": f"Agent token cannot {verb} another agent's prepared action.",
        "token_omitted": True,
    }


def prepared_action_route_access_error(
    *,
    action_id: Any,
    row: Any,
    identity: dict[str, Any],
    operation: str,
    enforce_agent_match: bool,
) -> tuple[dict[str, Any], int] | None:
    if row is None:
        if operation == "resume":
            return {"error": "prepared_action_not_found", "token_omitted": True}, 404
        return build_prepared_action_get_not_found_response(action_id), 404

    data = dict(row)
    requested_workspace = normalize_workspace_id(identity.get("workspace_id"))
    actual_workspace = normalize_workspace_id(data.get("workspace_id"))
    if actual_workspace != requested_workspace:
        return {
            "error": "forbidden",
            "message": f"prepared_action {action_id} belongs to workspace '{actual_workspace}', not '{requested_workspace}'.",
        }, 403

    if enforce_agent_match and data.get("requested_by_agent_id") != identity.get("agent_id"):
        return build_prepared_action_agent_forbidden_response(operation=operation), 403
    return None


def build_prepared_action_hash_mismatch_response(
    row: Any,
    current_action_hash: str | None = None,
    *,
    message: str,
    error: str = "action_hash_mismatch",
    approval: Any = None,
    include_prepared_action: bool = False,
) -> dict[str, Any]:
    data = dict(row) if row is not None else {}
    current_hash = current_action_hash or prepared_action_hash(data)
    payload: dict[str, Any] = {
        "error": error,
        "message": message,
        "stored_action_hash": data.get("action_hash"),
        "current_action_hash": current_hash,
        "token_omitted": True,
    }
    if approval is not None:
        payload["approval"] = dict(approval)
    if include_prepared_action:
        payload["prepared_action"] = prepared_action_public(data)
    return payload


def build_prepared_action_resume_blocked_response(
    *,
    action_id: Any,
    row: Any,
    approval: Any,
) -> tuple[dict[str, Any], int] | None:
    if row is None:
        return {"error": "prepared_action_not_found", "token_omitted": True}, 404

    data = dict(row)
    approval_data = dict(approval) if approval is not None else None
    if not approval_data or approval_data.get("decision") != "approved":
        return {
            "error": "approval_required",
            "message": "Prepared action can resume only after its approval is approved.",
            "approval_id": data.get("approval_id"),
            "decision": approval_data.get("decision") if approval_data else None,
            "token_omitted": True,
        }, 409
    expires_at = data.get("expires_at") or approval_data.get("expires_at")
    if expires_at:
        try:
            parsed_expiry = dt.datetime.fromisoformat(str(expires_at))
            if parsed_expiry.tzinfo is None:
                parsed_expiry = parsed_expiry.replace(tzinfo=dt.timezone.utc)
            if parsed_expiry <= dt.datetime.now(dt.timezone.utc):
                return {
                    "error": "prepared_action_expired",
                    "prepared_action_id": action_id,
                    "approval_id": data.get("approval_id"),
                    "token_omitted": True,
                }, 409
        except ValueError:
            return {
                "error": "prepared_action_expiry_invalid",
                "prepared_action_id": action_id,
                "token_omitted": True,
            }, 409
    if data.get("consumed_at") or data.get("status") == "consumed":
        return {
            "error": "prepared_action_already_consumed",
            "prepared_action": prepared_action_public(data),
            "token_omitted": True,
        }, 409

    verification = prepared_action_hash_verification(data)
    if not verification["match"]:
        return build_prepared_action_hash_mismatch_response(
            data,
            verification["current_action_hash"],
            message="Prepared action changed after approval; request a new approval.",
        ), 409
    return None


def build_prepared_action_resume_success_response(
    *,
    prepared_action: Any,
    approval: Any,
    provider_side_effect_id: str,
    hash_verification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": "agentops-approval-wall",
        "operation": "prepared_action_resume",
        "status": "completed",
        "prepared_action": prepared_action_public(prepared_action),
        "approval": dict(approval),
        "provider_side_effect_id": provider_side_effect_id,
        "execute_once": True,
        "hash_verification": hash_verification,
        "token_omitted": True,
    }


def build_prepared_action_provider_resume_request(
    prepared_action: Any,
    *,
    provider_side_effect_id: str,
    result_summary: str,
) -> dict[str, Any]:
    data = dict(prepared_action) if prepared_action is not None else {}
    return {
        "workspace_id": data.get("workspace_id"),
        "provider_side_effect_id": provider_side_effect_id,
        "result_summary": result_summary,
    }


def build_prepared_action_provider_result_fields(
    prepared_action: Any,
    resume_payload: Any,
    resume_status: int | None,
) -> dict[str, Any]:
    data = dict(prepared_action) if prepared_action is not None else {}
    payload = resume_payload if isinstance(resume_payload, dict) else {}
    return {
        "approval_id": data.get("approval_id"),
        "prepared_action": payload.get("prepared_action") or prepared_action_public(data),
        "prepared_action_resume_status": resume_status,
        "token_omitted": True,
    }


def runtime_probe_prepared_action_required_payload(
    *,
    prepared: dict[str, Any],
    provider: str,
    mode: str,
    task_id: str,
    prompt_hash: str,
) -> dict[str, Any]:
    return build_prepared_action_waiting_response(
        base={
            "provider": provider,
            "mode": mode,
            "dry_run": True,
            "live_probe_performed": False,
            "run_id": prepared.get("run_id"),
            "task_id": task_id,
            "tool_call_id": prepared.get("tool_call_id"),
            "prompt_hash": prompt_hash,
        },
        approval_wall=prepared.get("approval_wall") or {},
        reason="runtime_probe_prepared_action_required",
        resume_instruction="repeat the probe request with confirm_run:true and prepared_action_id={prepared_action_id}",
    )


def runtime_probe_blocked_payload(
    *,
    provider: str,
    mode: str,
    gate_error: dict[str, Any],
    created: bool | None = None,
) -> dict[str, Any]:
    payload = build_prepared_action_blocked_response(
        base={
            "provider": provider,
            "mode": mode,
            "dry_run": True,
            "live_probe_performed": False,
        },
        gate_error=gate_error,
    )
    if created is not None:
        payload["created"] = created
    return payload


def build_prepared_action_get_response(prepared_action: Any, approval: Any) -> dict[str, Any]:
    row = dict(prepared_action)
    verification = prepared_action_hash_verification(row)
    return {
        "provider": "agentops-approval-wall",
        "operation": "prepared_action_get",
        "status": "ready" if verification["match"] else "blocked",
        "prepared_action": prepared_action_public(row),
        "approval": dict(approval) if approval else None,
        "hash_verification": verification,
        "safety": {
            "read_only": True,
            "ledger_mutated": False,
            "raw_prompt_omitted": True,
            "raw_response_omitted": True,
            "token_omitted": True,
        },
        "token_omitted": True,
    }


def approval_wall_recommended_actions(approval: dict[str, Any], prepared_action: Any, approval_id: str) -> list[str]:
    row = dict(prepared_action) if prepared_action else None
    if approval.get("decision") == "pending":
        return [
            action
            for action in (
                f"agentops approval prepared-action get --action-id {row['action_id']}" if row else f"agentops approval inspect --approval-id {approval_id}",
                f"agentops approval approve --approval-id {approval_id}",
                f"agentops approval reject --approval-id {approval_id}",
                f"agentops approval prepared-action resume --action-id {row['action_id']} --provider-side-effect-id <id>" if row else "",
            )
            if action
        ]
    if row and approval.get("decision") == "approved" and not row.get("consumed_at"):
        return [f"agentops approval prepared-action resume --action-id {row['action_id']} --provider-side-effect-id <id>"]
    return ["agentops approval list --decision pending"]
