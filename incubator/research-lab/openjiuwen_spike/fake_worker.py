"""Deterministic in-memory worker for the openJiuwen protocol spike.

The worker does not import openJiuwen, execute tools, access files, load a
checkpoint, or write AgentOps MIS.  It exists solely to exercise the future
managed-subprocess contract.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Mapping

try:  # Support both direct execution and test imports from this directory.
    from .protocol import (
        MAX_TOTAL_BYTES,
        EventSequenceStore,
        IdempotencyStore,
        ProtocolError,
        decode_line,
        decode_stream,
        encode_message,
        receipt_events_for_request,
        validate_message,
    )
except ImportError:  # pragma: no cover - exercised by direct script execution.
    from protocol import (  # type: ignore[no-redef]
        MAX_TOTAL_BYTES,
        EventSequenceStore,
        IdempotencyStore,
        ProtocolError,
        decode_line,
        decode_stream,
        encode_message,
        receipt_events_for_request,
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

        events = receipt_events_for_request(validated)
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
