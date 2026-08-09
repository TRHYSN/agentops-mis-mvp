"""Deterministic in-memory worker for the openJiuwen protocol spike.

The worker does not import openJiuwen, execute tools, access files, load a
checkpoint, or write AgentOps MIS.  It exists solely to exercise the future
managed-subprocess contract.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from typing import Any, Mapping

try:  # Support both direct execution and test imports from this directory.
    from .protocol import (
        MAX_TOTAL_BYTES,
        EventSequenceStore,
        IdempotencyStore,
        PermissionDecision,
        ProtocolError,
        classify_permission,
        decode_line,
        decode_stream,
        encode_message,
        event_message,
        validate_message,
    )
except ImportError:  # pragma: no cover - exercised by direct script execution.
    from protocol import (  # type: ignore[no-redef]
        MAX_TOTAL_BYTES,
        EventSequenceStore,
        IdempotencyStore,
        PermissionDecision,
        ProtocolError,
        classify_permission,
        decode_line,
        decode_stream,
        encode_message,
        event_message,
        validate_message,
    )


@dataclass(frozen=True)
class ProcessResult:
    events: tuple[dict[str, Any], ...]
    replayed: bool


class FakeWorker:
    """Process validated requests with deterministic, fail-closed receipts."""

    def __init__(self) -> None:
        self.idempotency = IdempotencyStore()
        self.event_sequences = EventSequenceStore()
        self.transition_counts = {"action": 0, "cancel": 0, "resume": 0}

    def process_request(self, request: Mapping[str, Any]) -> ProcessResult:
        validated = validate_message(dict(request))
        if validated["kind"] != "request":
            raise ProtocolError("request_required", "fake worker accepts only requests")
        prior = self.idempotency.lookup(validated)
        if prior is not None:
            return ProcessResult(events=prior.events, replayed=True)

        events = self._build_events(validated)
        receipt = self.idempotency.commit(validated, events)
        for event in receipt.events:
            applied = self.event_sequences.apply(event)
            if not applied.applied:
                raise ProtocolError("internal_duplicate_event", "new receipt contained a duplicate event")

        operation = validated["operation"]
        if operation == "action.propose":
            self.transition_counts["action"] += 1
        else:
            self.transition_counts[operation] += 1
        return ProcessResult(events=receipt.events, replayed=False)

    def process_line(self, raw: bytes | str) -> ProcessResult:
        return self.process_request(decode_line(raw))

    def process_stream(self, raw: bytes) -> bytes:
        output = bytearray()
        for request in decode_stream(raw):
            result = self.process_request(request)
            for event in result.events:
                output.extend(encode_message(event))
                if len(output) > MAX_TOTAL_BYTES:
                    raise ProtocolError("output_too_large", "worker output exceeds the total byte limit")
        return bytes(output)

    def _build_events(self, request: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        operation = request["operation"]
        payload = request["payload"]
        payloads: list[tuple[str, dict[str, Any]]] = [
            ("request.accepted", {"operation": operation})
        ]
        if operation == "action.propose":
            permission = classify_permission(payload["action_type"])
            payloads.append(
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
                payloads.append(
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
                payloads.append(
                    (
                        "action.awaiting_approval",
                        {
                            "action_id": payload["action_id"],
                            "permission_request_id": _stable_id(
                                "perm", request["request_id"], payload["action_id"]
                            ),
                            "required_decision": "human_approval",
                            "effect_performed": False,
                        },
                    )
                )
            else:
                payloads.append(
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
            payloads.extend((("cancel.requested", dict(common)), ("cancel.accepted", dict(common))))
        elif operation == "resume":
            common = {
                "target_request_id": payload["target_request_id"],
                "checkpoint_id": payload["checkpoint_id"],
                "expected_sequence": payload["expected_sequence"],
                "effect_performed": False,
            }
            payloads.extend((("resume.requested", dict(common)), ("resume.accepted", dict(common))))
        else:  # validate_message makes this unreachable and keeps the worker fail closed.
            raise ProtocolError("unknown_operation", "request operation is not supported")

        return tuple(
            event_message(
                event_id=_stable_id("evt", request["request_id"], str(sequence), event_type),
                request_id=request["request_id"],
                sequence=sequence,
                event_type=event_type,
                payload=event_payload,
            )
            for sequence, (event_type, event_payload) in enumerate(payloads)
        )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def main() -> int:
    worker = FakeWorker()
    raw = sys.stdin.buffer.read(MAX_TOTAL_BYTES + 1)
    try:
        if len(raw) > MAX_TOTAL_BYTES:
            raise ProtocolError("stream_too_large", "JSONL stream exceeds the total byte limit")
        sys.stdout.buffer.write(worker.process_stream(raw))
    except ProtocolError as exc:
        error = {"ok": False, "error_code": exc.code, "input_omitted": True}
        sys.stderr.write(json.dumps(error, sort_keys=True) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
