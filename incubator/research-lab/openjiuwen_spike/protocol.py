"""Bounded JSONL contract for the no-install openJiuwen compatibility spike.

This module is deliberately independent of openJiuwen and AgentOps MIS.  It
models the narrow subprocess boundary that a later, separately approved
integration may implement.  It does not grant approvals, load checkpoints, or
perform external actions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "openjiuwen_spike_protocol_v1"
MAX_LINE_BYTES = 16_384
MAX_TOTAL_BYTES = 65_536
MAX_MESSAGES = 64
MAX_PAYLOAD_BYTES = 8_192
MAX_IDEMPOTENCY_RECEIPTS = MAX_MESSAGES
MAX_REQUEST_IDENTITIES = MAX_MESSAGES
MAX_EVENT_IDENTITIES = MAX_MESSAGES * 3
MAX_EVENT_STREAMS = MAX_MESSAGES
MAX_RECEIPT_EVENTS = 3
MAX_ID_CHARS = 128
MAX_KEY_CHARS = 64
MAX_STRING_CHARS = 2_048
MAX_COLLECTION_ITEMS = 64
MAX_NESTING_DEPTH = 6

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)^\s*bearer\s+\S+"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9._~+/=-]{8,}\b"),
    re.compile(r"\b(?:ntn_|agtok_|agtsess_)[A-Za-z0-9._~+/=-]{8,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
)
_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "messages",
    "password",
    "prompt",
    "raw_prompt",
    "raw_response",
    "response",
    "secret",
    "secrets",
    "token",
    "transcript",
}

REQUEST_OPERATIONS = frozenset({"action.propose", "cancel", "resume"})
EVENT_TYPES = frozenset(
    {
        "request.accepted",
        "permission.decision",
        "action.completed",
        "action.awaiting_approval",
        "action.denied",
        "cancel.requested",
        "cancel.accepted",
        "resume.requested",
        "resume.accepted",
    }
)


class ProtocolError(ValueError):
    """Fail-closed protocol validation error with a bounded reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionResult:
    decision: PermissionDecision
    reason_code: str


@dataclass(frozen=True)
class StoredReceipt:
    fingerprint: str
    request_id: str
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class EventApplyResult:
    applied: bool
    duplicate: bool


ALLOW_ACTIONS = frozenset({"research.metadata.read", "research.evidence.read"})
ASK_ACTIONS = frozenset(
    {
        "research.trial.propose",
        "research.remote.submit",
        "dependency.install",
    }
)
DENY_ACTIONS = frozenset(
    {
        "artifact.delete",
        "authority.approve",
        "credential.read",
        "memory.promote",
        "system.shell",
    }
)


def classify_permission(action_type: str) -> PermissionResult:
    """Classify a proposed action without accepting caller-supplied authority."""

    _validate_identifier(action_type, "action_type")
    if action_type in ALLOW_ACTIONS:
        return PermissionResult(PermissionDecision.ALLOW, "explicit_read_only_allow")
    if action_type in ASK_ACTIONS:
        return PermissionResult(PermissionDecision.ASK, "human_approval_required")
    if action_type in DENY_ACTIONS:
        return PermissionResult(PermissionDecision.DENY, "explicit_deny")
    return PermissionResult(PermissionDecision.DENY, "unknown_action_default_deny")


def canonical_json(value: Any) -> str:
    """Return a deterministic JSON encoding after all safety checks pass."""

    _validate_tree(value, path="$", depth=0)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError("invalid_json_value", "value is not canonical JSON") from exc


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def decode_line(raw: bytes | str) -> dict[str, Any]:
    """Decode one exact canonical, newline-terminated JSONL record."""

    try:
        encoded = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    except UnicodeEncodeError as exc:
        raise ProtocolError("invalid_unicode", "JSONL record contains an invalid Unicode scalar") from exc
    if len(encoded) > MAX_LINE_BYTES:
        raise ProtocolError("line_too_large", "JSONL record exceeds the byte limit")
    if not encoded.endswith(b"\n"):
        raise ProtocolError("partial_record", "JSONL record must end with a newline")
    body = encoded[:-1]
    if b"\n" in body or b"\r" in body:
        raise ProtocolError("multiple_records", "decode_line accepts exactly one record")
    if not body:
        raise ProtocolError("empty_record", "JSONL record is empty")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8", "JSONL record is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ProtocolError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProtocolError("invalid_json", "JSONL record is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("non_object", "top-level JSONL record must be an object")
    validated = validate_message(value)
    canonical_record = (canonical_json(validated) + "\n").encode("utf-8")
    if encoded != canonical_record:
        raise ProtocolError(
            "noncanonical_encoding",
            "JSONL record must exactly match its canonical UTF-8 encoding",
        )
    return validated


def decode_stream(raw: bytes) -> list[dict[str, Any]]:
    """Decode a complete bounded JSONL byte stream.

    A non-empty stream must end with a newline.  This makes a trailing partial
    record distinguishable from a complete record after a process interruption.
    """

    if len(raw) > MAX_TOTAL_BYTES:
        raise ProtocolError("stream_too_large", "JSONL stream exceeds the total byte limit")
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ProtocolError("partial_record", "JSONL stream ends with a partial record")
    records = raw.splitlines(keepends=True)
    if len(records) > MAX_MESSAGES:
        raise ProtocolError("too_many_records", "JSONL stream exceeds the record limit")
    return [decode_line(record) for record in records]


def encode_message(message: Mapping[str, Any]) -> bytes:
    validated = validate_message(dict(message))
    encoded = (canonical_json(validated) + "\n").encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise ProtocolError("line_too_large", "encoded JSONL record exceeds the byte limit")
    return encoded


def validate_message(message: dict[str, Any]) -> dict[str, Any]:
    _validate_tree(message, path="$", depth=0)
    kind = message.get("kind")
    if kind == "request":
        _validate_request(message)
    elif kind == "event":
        _validate_event(message)
    else:
        raise ProtocolError("unknown_kind", "kind must be request or event")
    return message


def event_message(
    *,
    event_id: str,
    request_id: str,
    sequence: int,
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    event = {
        "schema_version": SCHEMA_VERSION,
        "kind": "event",
        "event_id": event_id,
        "request_id": request_id,
        "sequence": sequence,
        "event_type": event_type,
        "payload": dict(payload),
    }
    return validate_message(event)


def receipt_events_for_request(request: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Build the only semantically valid fake-worker receipt for a request."""

    validated = validate_message(dict(request))
    if validated["kind"] != "request":
        raise ProtocolError("request_required", "receipt generation requires a request")
    operation = validated["operation"]
    payload = validated["payload"]
    event_payloads: list[tuple[str, dict[str, Any]]] = [
        ("request.accepted", {"operation": operation})
    ]
    if operation == "action.propose":
        permission = classify_permission(payload["action_type"])
        event_payloads.append(
            (
                "permission.decision",
                {
                    "action_id": payload["action_id"],
                    "action_type": payload["action_type"],
                    "decision": permission.decision.value,
                    "reason_code": permission.reason_code,
                },
            )
        )
        if permission.decision is PermissionDecision.ALLOW:
            event_payloads.append(
                (
                    "action.completed",
                    {
                        "action_id": payload["action_id"],
                        "result_summary": "bounded_read_only_receipt",
                        "side_effect_performed": False,
                    },
                )
            )
        elif permission.decision is PermissionDecision.ASK:
            event_payloads.append(
                (
                    "action.awaiting_approval",
                    {
                        "action_id": payload["action_id"],
                        "permission_request_id": stable_protocol_id(
                            "perm", validated["request_id"], payload["action_id"]
                        ),
                        "required_decision": "human_approval",
                        "effect_performed": False,
                    },
                )
            )
        else:
            event_payloads.append(
                (
                    "action.denied",
                    {
                        "action_id": payload["action_id"],
                        "reason_code": permission.reason_code,
                        "effect_performed": False,
                    },
                )
            )
    elif operation == "cancel":
        common = {
            "target_request_id": payload["target_request_id"],
            "effect_performed": False,
        }
        event_payloads.extend((("cancel.requested", dict(common)), ("cancel.accepted", dict(common))))
    elif operation == "resume":
        common = {
            "target_request_id": payload["target_request_id"],
            "checkpoint_id": payload["checkpoint_id"],
            "expected_sequence": payload["expected_sequence"],
            "effect_performed": False,
        }
        event_payloads.extend((("resume.requested", dict(common)), ("resume.accepted", dict(common))))
    else:  # validate_message makes this unreachable.
        raise ProtocolError("unknown_operation", "request operation is not supported")

    return tuple(
        event_message(
            event_id=stable_protocol_id(
                "evt", validated["request_id"], str(sequence), event_type
            ),
            request_id=validated["request_id"],
            sequence=sequence,
            event_type=event_type,
            payload=event_payload,
        )
        for sequence, (event_type, event_payload) in enumerate(event_payloads)
    )


def stable_protocol_id(prefix: str, *parts: str) -> str:
    """Return a bounded deterministic identifier for protocol-owned receipts."""

    _validate_identifier(prefix, "id_prefix")
    for index, part in enumerate(parts):
        if not isinstance(part, str):
            raise ProtocolError("invalid_identifier_input", f"id part {index} must be a string")
        _reject_surrogates(part)
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


class IdempotencyStore:
    """In-memory receipt store; it is not durable MIS or runtime state."""

    def __init__(
        self,
        *,
        max_receipts: int = MAX_IDEMPOTENCY_RECEIPTS,
        max_request_ids: int = MAX_REQUEST_IDENTITIES,
    ) -> None:
        self._max_receipts = _validate_store_limit(
            max_receipts, MAX_IDEMPOTENCY_RECEIPTS, "max_receipts"
        )
        self._max_request_ids = _validate_store_limit(
            max_request_ids, MAX_REQUEST_IDENTITIES, "max_request_ids"
        )
        self._by_key: dict[str, StoredReceipt] = {}
        self._request_fingerprints: dict[str, str] = {}
        self._request_keys: dict[str, str] = {}

    def lookup(self, request: Mapping[str, Any]) -> StoredReceipt | None:
        validated = validate_message(dict(request))
        request_id = validated["request_id"]
        fingerprint = request_fingerprint(validated)
        key = validated["payload"]["idempotency_key"]
        prior_request = self._request_fingerprints.get(request_id)
        if prior_request is not None and prior_request != fingerprint:
            raise ProtocolError("request_id_conflict", "request_id was reused with changed content")
        if prior_request is not None and self._request_keys[request_id] != key:
            raise ProtocolError("request_id_conflict", "request_id was reused with a new idempotency_key")
        receipt = self._by_key.get(key)
        if receipt is not None and receipt.fingerprint != fingerprint:
            raise ProtocolError(
                "idempotency_conflict",
                "idempotency_key was reused with changed operation or payload",
            )
        if prior_request is None and len(self._request_fingerprints) >= self._max_request_ids:
            raise ProtocolError(
                "store_capacity_exceeded",
                "idempotency request identity capacity is exhausted",
            )
        if receipt is None and len(self._by_key) >= self._max_receipts:
            raise ProtocolError(
                "store_capacity_exceeded",
                "idempotency receipt capacity is exhausted",
            )
        if receipt is not None:
            self._request_fingerprints[request_id] = fingerprint
            self._request_keys[request_id] = key
            return _clone_receipt(receipt)
        return None

    def commit(self, request: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> StoredReceipt:
        validated = validate_message(dict(request))
        if self.lookup(validated) is not None:
            raise ProtocolError("idempotency_already_committed", "receipt already exists")
        key = validated["payload"]["idempotency_key"]
        if len(self._by_key) >= self._max_receipts:
            raise ProtocolError("store_capacity_exceeded", "idempotency receipt capacity is exhausted")
        if (
            validated["request_id"] not in self._request_fingerprints
            and len(self._request_fingerprints) >= self._max_request_ids
        ):
            raise ProtocolError(
                "store_capacity_exceeded",
                "idempotency request identity capacity is exhausted",
            )
        copied_event_list: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            if index >= MAX_RECEIPT_EVENTS:
                raise ProtocolError("receipt_too_large", "receipt exceeds the event limit")
            validated_event = validate_message(dict(event))
            if validated_event["kind"] != "event":
                raise ProtocolError("event_required", "receipt entries must be events")
            if validated_event["request_id"] != validated["request_id"]:
                raise ProtocolError("receipt_request_mismatch", "receipt event request_id is not bound")
            if validated_event["sequence"] != index:
                raise ProtocolError("receipt_sequence_mismatch", "receipt events must be contiguous")
            copied_event_list.append(validated_event)
        if len(copied_event_list) != MAX_RECEIPT_EVENTS:
            raise ProtocolError("receipt_event_count", "receipt must contain exactly three events")
        expected_events = receipt_events_for_request(validated)
        if copied_event_list != list(expected_events):
            raise ProtocolError(
                "receipt_semantic_mismatch",
                "receipt events do not exactly match the originating request",
            )
        receipt = StoredReceipt(
            fingerprint=request_fingerprint(validated),
            request_id=validated["request_id"],
            events=expected_events,
        )
        self._request_fingerprints[validated["request_id"]] = receipt.fingerprint
        self._request_keys[validated["request_id"]] = key
        self._by_key[key] = receipt
        return _clone_receipt(receipt)


class EventSequenceStore:
    """Reject duplicate, conflicting, and out-of-order event application."""

    def __init__(
        self,
        *,
        max_events: int = MAX_EVENT_IDENTITIES,
        max_requests: int = MAX_EVENT_STREAMS,
    ) -> None:
        self._max_events = _validate_store_limit(max_events, MAX_EVENT_IDENTITIES, "max_events")
        self._max_requests = _validate_store_limit(max_requests, MAX_EVENT_STREAMS, "max_requests")
        self._next_sequence: dict[str, int] = {}
        self._event_fingerprints: dict[str, str] = {}

    def apply(self, event: Mapping[str, Any]) -> EventApplyResult:
        validated = validate_message(dict(event))
        if validated["kind"] != "event":
            raise ProtocolError("event_required", "sequence store accepts only events")
        event_id = validated["event_id"]
        fingerprint = canonical_digest(validated)
        prior = self._event_fingerprints.get(event_id)
        if prior is not None:
            if prior != fingerprint:
                raise ProtocolError("event_id_conflict", "event_id was reused with changed content")
            return EventApplyResult(applied=False, duplicate=True)
        request_id = validated["request_id"]
        if len(self._event_fingerprints) >= self._max_events:
            raise ProtocolError("store_capacity_exceeded", "event identity capacity is exhausted")
        if request_id not in self._next_sequence and len(self._next_sequence) >= self._max_requests:
            raise ProtocolError("store_capacity_exceeded", "event stream capacity is exhausted")
        expected = self._next_sequence.get(request_id, 0)
        if validated["sequence"] != expected:
            raise ProtocolError(
                "event_out_of_order",
                f"event sequence must equal the next expected value ({expected})",
            )
        self._event_fingerprints[event_id] = fingerprint
        self._next_sequence[request_id] = expected + 1
        return EventApplyResult(applied=True, duplicate=False)


def request_fingerprint(request: Mapping[str, Any]) -> str:
    """Bind an idempotency key to operation and effect-bearing payload.

    request_id and idempotency_key are routing/deduplication fields, so a retry
    under a new request_id can replay the original receipt if all effect-bearing
    fields are identical.
    """

    validated = validate_message(dict(request))
    payload = dict(validated["payload"])
    payload.pop("idempotency_key")
    return canonical_digest({"operation": validated["operation"], "payload": payload})


def _validate_request(message: dict[str, Any]) -> None:
    _require_exact_keys(
        message,
        {"schema_version", "kind", "request_id", "operation", "payload"},
        "request",
    )
    _require_schema(message)
    _validate_identifier(message["request_id"], "request_id")
    operation = message["operation"]
    if not isinstance(operation, str) or operation not in REQUEST_OPERATIONS:
        raise ProtocolError("unknown_operation", "request operation is not supported")
    payload = message["payload"]
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_payload", "request payload must be an object")
    _validate_payload_size(payload)
    if operation == "action.propose":
        _require_exact_keys(
            payload,
            {"action_id", "action_type", "resource_id", "arguments", "idempotency_key"},
            "action payload",
        )
        for key in ("action_id", "action_type", "resource_id", "idempotency_key"):
            _validate_identifier(payload[key], key)
        if not isinstance(payload["arguments"], dict):
            raise ProtocolError("invalid_arguments", "action arguments must be an object")
    elif operation == "cancel":
        _require_exact_keys(payload, {"target_request_id", "idempotency_key"}, "cancel payload")
        _validate_identifier(payload["target_request_id"], "target_request_id")
        _validate_identifier(payload["idempotency_key"], "idempotency_key")
    elif operation == "resume":
        _require_exact_keys(
            payload,
            {"target_request_id", "checkpoint_id", "expected_sequence", "idempotency_key"},
            "resume payload",
        )
        for key in ("target_request_id", "checkpoint_id", "idempotency_key"):
            _validate_identifier(payload[key], key)
        _validate_nonnegative_int(payload["expected_sequence"], "expected_sequence")


def _validate_event(message: dict[str, Any]) -> None:
    _require_exact_keys(
        message,
        {"schema_version", "kind", "event_id", "request_id", "sequence", "event_type", "payload"},
        "event",
    )
    _require_schema(message)
    _validate_identifier(message["event_id"], "event_id")
    _validate_identifier(message["request_id"], "request_id")
    _validate_nonnegative_int(message["sequence"], "sequence")
    event_type = message["event_type"]
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise ProtocolError("unknown_event_type", "event type is not supported")
    payload = message["payload"]
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_payload", "event payload must be an object")
    _validate_payload_size(payload)
    _validate_event_payload(event_type, payload)


def _validate_event_payload(event_type: str, payload: dict[str, Any]) -> None:
    schemas: dict[str, set[str]] = {
        "request.accepted": {"operation"},
        "permission.decision": {"action_id", "action_type", "decision", "reason_code"},
        "action.completed": {"action_id", "result_summary", "side_effect_performed"},
        "action.awaiting_approval": {
            "action_id",
            "permission_request_id",
            "required_decision",
            "effect_performed",
        },
        "action.denied": {"action_id", "reason_code", "effect_performed"},
        "cancel.requested": {"target_request_id", "effect_performed"},
        "cancel.accepted": {"target_request_id", "effect_performed"},
        "resume.requested": {
            "target_request_id",
            "checkpoint_id",
            "expected_sequence",
            "effect_performed",
        },
        "resume.accepted": {
            "target_request_id",
            "checkpoint_id",
            "expected_sequence",
            "effect_performed",
        },
    }
    _require_exact_keys(payload, schemas[event_type], f"{event_type} payload")
    for key in (
        "action_id",
        "action_type",
        "permission_request_id",
        "target_request_id",
        "checkpoint_id",
    ):
        if key in payload:
            _validate_identifier(payload[key], key)
    if event_type == "request.accepted":
        operation = payload["operation"]
        if not isinstance(operation, str) or operation not in REQUEST_OPERATIONS:
            raise ProtocolError("unknown_operation", "accepted operation is not supported")
    if event_type == "permission.decision":
        decision = payload["decision"]
        if not isinstance(decision, str) or decision not in {item.value for item in PermissionDecision}:
            raise ProtocolError("invalid_permission", "permission decision must be allow, ask, or deny")
        _validate_identifier(payload["reason_code"], "reason_code")
        classified = classify_permission(payload["action_type"])
        if decision != classified.decision.value or payload["reason_code"] != classified.reason_code:
            raise ProtocolError(
                "permission_classifier_mismatch",
                "permission decision and reason must match the fail-closed classifier",
            )
    if "reason_code" in payload:
        _validate_identifier(payload["reason_code"], "reason_code")
    if "result_summary" in payload and not isinstance(payload["result_summary"], str):
        raise ProtocolError("invalid_result_summary", "result_summary must be a string")
    if "required_decision" in payload and payload["required_decision"] != "human_approval":
        raise ProtocolError("invalid_required_decision", "only human_approval may satisfy ASK")
    if "expected_sequence" in payload:
        _validate_nonnegative_int(payload["expected_sequence"], "expected_sequence")
    for key in ("side_effect_performed", "effect_performed"):
        if key in payload and payload[key] is not False:
            raise ProtocolError("effect_not_allowed", "the compatibility worker cannot perform effects")


def _validate_tree(value: Any, *, path: str, depth: int) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolError("nesting_too_deep", "JSON value exceeds the nesting limit")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 2**63 - 1:
            raise ProtocolError("integer_out_of_range", "integer exceeds signed 64-bit range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError("non_finite_number", "non-finite numbers are forbidden")
        return
    if isinstance(value, str):
        _reject_surrogates(value)
        if len(value) > MAX_STRING_CHARS:
            raise ProtocolError("string_too_large", "string exceeds the character limit")
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                raise ProtocolError("secret_value_forbidden", "secret-shaped string is forbidden")
        return
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ProtocolError("collection_too_large", "object exceeds the item limit")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("invalid_key", "object keys must be strings")
            _reject_surrogates(key)
            if not key or len(key) > MAX_KEY_CHARS:
                raise ProtocolError("invalid_key", "object key length is invalid")
            if _is_sensitive_key(key):
                raise ProtocolError("sensitive_key_forbidden", "raw/private field is forbidden")
            _validate_tree(item, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ProtocolError("collection_too_large", "array exceeds the item limit")
        for index, item in enumerate(value):
            _validate_tree(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    raise ProtocolError("invalid_type", f"unsupported JSON type at {path}")


def _is_sensitive_key(key: str) -> bool:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", snake.lower()).strip("_")
    components = set(normalized.split("_"))
    sensitive_components = {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "messages",
        "password",
        "prompt",
        "response",
        "secret",
        "secrets",
        "token",
        "transcript",
    }
    joined = "".join(normalized.split("_"))
    sensitive_suffixes = (
        "apikey",
        "accesskey",
        "accesskeyid",
        "authorization",
        "clientsecret",
        "credential",
        "credentials",
        "password",
        "privatekey",
        "secretkey",
    )
    return (
        normalized in _SENSITIVE_KEYS
        or bool(components & sensitive_components)
        or any(joined.endswith(compound) for compound in sensitive_suffixes)
    )


def _reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ProtocolError("invalid_unicode", "unpaired Unicode surrogates are forbidden")


def _clone_receipt(receipt: StoredReceipt) -> StoredReceipt:
    events = tuple(json.loads(canonical_json(event)) for event in receipt.events)
    return StoredReceipt(
        fingerprint=receipt.fingerprint,
        request_id=receipt.request_id,
        events=events,
    )


def _validate_store_limit(value: Any, hard_maximum: int, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > hard_maximum
    ):
        raise ProtocolError(
            "invalid_store_limit",
            f"{field} must be between 1 and the protocol hard maximum",
        )
    return value


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_key", "duplicate JSON object keys are forbidden")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ProtocolError("non_finite_number", f"JSON constant {value} is forbidden")


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail = "schema mismatch"
        if missing:
            detail += f"; missing={','.join(missing)}"
        if unknown:
            detail += f"; unknown={','.join(unknown)}"
        raise ProtocolError("unknown_or_missing_fields", f"{label} {detail}")


def _require_schema(message: Mapping[str, Any]) -> None:
    if message.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported_schema", "schema_version is not supported")


def _validate_identifier(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ProtocolError("invalid_identifier", f"{field} must be a string")
    if not value or len(value) > MAX_ID_CHARS or _ID_RE.fullmatch(value) is None:
        raise ProtocolError("invalid_identifier", f"{field} is not a bounded identifier")


def _validate_nonnegative_int(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**31 - 1:
        raise ProtocolError("invalid_integer", f"{field} must be a bounded non-negative integer")


def _validate_payload_size(payload: Mapping[str, Any]) -> None:
    if len(canonical_json(payload).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ProtocolError("payload_too_large", "payload exceeds the byte limit")
