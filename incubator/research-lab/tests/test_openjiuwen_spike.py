from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SPIKE_ROOT = Path(__file__).resolve().parents[1] / "openjiuwen_spike"
if str(SPIKE_ROOT) not in sys.path:
    sys.path.insert(0, str(SPIKE_ROOT))

from fake_worker import FakeWorker  # noqa: E402
from protocol import (  # noqa: E402
    MAX_COLLECTION_ITEMS,
    MAX_ID_CHARS,
    MAX_LINE_BYTES,
    MAX_MESSAGES,
    MAX_PAYLOAD_BYTES,
    MAX_STRING_CHARS,
    MAX_TOTAL_BYTES,
    SCHEMA_VERSION,
    EventSequenceStore,
    PermissionDecision,
    ProtocolError,
    canonical_json,
    classify_permission,
    decode_line,
    decode_stream,
    encode_message,
    event_message,
    validate_message,
)


def action_request(
    *,
    request_id: str = "req_read_1",
    idempotency_key: str = "idem_read_1",
    action_type: str = "research.metadata.read",
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "request",
        "request_id": request_id,
        "operation": "action.propose",
        "payload": {
            "action_id": "act_read_1",
            "action_type": action_type,
            "resource_id": "experiment_exp_1",
            "arguments": arguments or {"selector": "bounded_metadata"},
            "idempotency_key": idempotency_key,
        },
    }


def control_request(
    operation: str,
    *,
    request_id: str,
    idempotency_key: str,
    target_request_id: str = "req_target_1",
    checkpoint_id: str = "checkpoint_ref_1",
    expected_sequence: int = 7,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "target_request_id": target_request_id,
        "idempotency_key": idempotency_key,
    }
    if operation == "resume":
        payload.update(
            {
                "checkpoint_id": checkpoint_id,
                "expected_sequence": expected_sequence,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "request",
        "request_id": request_id,
        "operation": operation,
        "payload": payload,
    }


class DependencyManifestTests(unittest.TestCase):
    def test_manifest_pins_verified_metadata_and_marks_execution_not_run(self) -> None:
        manifest = json.loads((SPIKE_ROOT / "dependency-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["openjiuwen"]["source_commit"],
            "bf0a3eb2c70fcbae404403530519ca02e7fc4692",
        )
        self.assertEqual(manifest["openjiuwen"]["github_release"], "v0.1.16")
        self.assertEqual(manifest["openjiuwen"]["pypi_version_observed"], "0.1.16.post2")
        self.assertEqual(manifest["openjiuwen"]["python_requires"], ">=3.11,<3.14")
        self.assertEqual(manifest["openjiuwen"]["license"], "Apache-2.0")
        self.assertIn("NOTICE", manifest["openjiuwen"]["notice_files"])
        self.assertFalse(manifest["execution"]["installed"])
        self.assertFalse(manifest["execution"]["imported"])
        self.assertEqual(manifest["execution"]["real_runtime"], "NOT_RUN")
        self.assertEqual(manifest["unknowns"]["commit_pypi_equivalence"], "UNKNOWN")
        self.assertEqual(manifest["excluded"]["jiuwenswarm"], "NOT_INTEGRATED")


class ProtocolValidationTests(unittest.TestCase):
    def assert_protocol_error(self, code: str, callback) -> None:
        with self.assertRaises(ProtocolError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_canonical_round_trip_is_bounded_and_deterministic(self) -> None:
        request = action_request()
        encoded = encode_message(request)

        self.assertLessEqual(len(encoded), MAX_LINE_BYTES)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(decode_line(encoded), request)
        self.assertEqual(
            canonical_json({"b": 2, "a": 1}),
            '{"a":1,"b":2}',
        )

    def test_invalid_utf8_json_non_object_and_duplicate_keys_fail_closed(self) -> None:
        self.assert_protocol_error("invalid_utf8", lambda: decode_line(b"\xff\n"))
        self.assert_protocol_error("invalid_json", lambda: decode_line(b"{not-json}\n"))
        self.assert_protocol_error("non_object", lambda: decode_line(b"[]\n"))
        duplicate = (
            '{"schema_version":"%s","kind":"request","kind":"event"}\n'
            % SCHEMA_VERSION
        ).encode()
        self.assert_protocol_error("duplicate_key", lambda: decode_line(duplicate))

    def test_unknown_fields_operations_event_types_and_types_fail_closed(self) -> None:
        unknown = action_request()
        unknown["unexpected"] = False
        self.assert_protocol_error("unknown_or_missing_fields", lambda: validate_message(unknown))

        wrong_type = action_request()
        wrong_type["request_id"] = 4
        self.assert_protocol_error("invalid_identifier", lambda: validate_message(wrong_type))

        wrong_operation = action_request()
        wrong_operation["operation"] = "runtime.anything"
        self.assert_protocol_error("unknown_operation", lambda: validate_message(wrong_operation))

        event = event_message(
            event_id="evt_1",
            request_id="req_1",
            sequence=0,
            event_type="request.accepted",
            payload={"operation": "cancel"},
        )
        event["event_type"] = "unbounded.event"
        self.assert_protocol_error("unknown_event_type", lambda: validate_message(event))

        bad_permission = event_message(
            event_id="evt_bad_permission",
            request_id="req_bad_permission",
            sequence=0,
            event_type="permission.decision",
            payload={
                "action_id": "act_1",
                "action_type": "research.metadata.read",
                "decision": "allow",
                "reason_code": "explicit_read_only_allow",
            },
        )
        bad_permission["payload"]["decision"] = []
        self.assert_protocol_error("invalid_permission", lambda: validate_message(bad_permission))

    def test_non_finite_and_non_json_values_fail_closed(self) -> None:
        self.assert_protocol_error(
            "non_finite_number",
            lambda: decode_line(b'{"value":NaN}\n'),
        )
        request = action_request(arguments={"bad": float("inf")})
        self.assert_protocol_error("non_finite_number", lambda: validate_message(request))
        request = action_request(arguments={"bad": (1, 2)})
        self.assert_protocol_error("invalid_type", lambda: validate_message(request))

    def test_line_stream_record_and_partial_bounds(self) -> None:
        self.assert_protocol_error(
            "line_too_large",
            lambda: decode_line(b"x" * (MAX_LINE_BYTES + 1)),
        )
        self.assert_protocol_error(
            "stream_too_large",
            lambda: decode_stream(b"x" * (MAX_TOTAL_BYTES + 1)),
        )
        encoded = encode_message(action_request())
        self.assert_protocol_error(
            "too_many_records",
            lambda: decode_stream(encoded * (MAX_MESSAGES + 1)),
        )
        self.assert_protocol_error("partial_record", lambda: decode_stream(encoded.rstrip(b"\n")))

    def test_identifier_string_collection_nesting_and_payload_bounds(self) -> None:
        request = action_request(request_id="r" * (MAX_ID_CHARS + 1))
        self.assert_protocol_error("invalid_identifier", lambda: validate_message(request))

        request = action_request(arguments={"text": "x" * (MAX_STRING_CHARS + 1)})
        self.assert_protocol_error("string_too_large", lambda: validate_message(request))

        request = action_request(arguments={"items": list(range(MAX_COLLECTION_ITEMS + 1))})
        self.assert_protocol_error("collection_too_large", lambda: validate_message(request))

        nested: dict[str, object] = {"leaf": True}
        for index in range(8):
            nested = {f"n{index}": nested}
        request = action_request(arguments=nested)
        self.assert_protocol_error("nesting_too_deep", lambda: validate_message(request))

        arguments = {f"field_{index}": "x" * 512 for index in range(20)}
        request = action_request(arguments=arguments)
        self.assertGreater(len(canonical_json(request["payload"]).encode()), MAX_PAYLOAD_BYTES)
        self.assert_protocol_error("payload_too_large", lambda: validate_message(request))

    def test_sensitive_keys_and_secret_shaped_values_are_rejected_recursively(self) -> None:
        request = action_request(arguments={"nested": {"raw_prompt": "omitted"}})
        self.assert_protocol_error("sensitive_key_forbidden", lambda: validate_message(request))

        request = action_request(arguments={"accessToken": "omitted"})
        self.assert_protocol_error("sensitive_key_forbidden", lambda: validate_message(request))

        request = action_request(arguments={"header": "Bearer fixture-value"})
        self.assert_protocol_error("secret_value_forbidden", lambda: validate_message(request))

    def test_permission_cannot_be_injected_as_approval(self) -> None:
        request = action_request(action_type="research.remote.submit")
        request["payload"]["approval_granted"] = True
        self.assert_protocol_error("unknown_or_missing_fields", lambda: validate_message(request))


class PermissionAndWorkerTests(unittest.TestCase):
    def test_permission_policy_is_allow_ask_and_default_deny(self) -> None:
        self.assertEqual(
            classify_permission("research.metadata.read").decision,
            PermissionDecision.ALLOW,
        )
        self.assertEqual(
            classify_permission("research.remote.submit").decision,
            PermissionDecision.ASK,
        )
        self.assertEqual(
            classify_permission("unregistered.action").decision,
            PermissionDecision.DENY,
        )

    def test_valid_read_only_action_has_bounded_no_side_effect_receipt(self) -> None:
        worker = FakeWorker()
        result = worker.process_request(action_request())

        self.assertFalse(result.replayed)
        self.assertEqual(
            [event["event_type"] for event in result.events],
            ["request.accepted", "permission.decision", "action.completed"],
        )
        self.assertEqual(result.events[1]["payload"]["decision"], "allow")
        self.assertFalse(result.events[2]["payload"]["side_effect_performed"])
        encoded = b"".join(encode_message(event) for event in result.events).decode()
        for forbidden in ("raw_prompt", "raw_response", "credential", "Bearer "):
            self.assertNotIn(forbidden, encoded)

    def test_ask_is_a_permission_request_not_an_approval(self) -> None:
        worker = FakeWorker()
        result = worker.process_request(action_request(action_type="research.remote.submit"))

        self.assertEqual(result.events[1]["payload"]["decision"], "ask")
        self.assertEqual(result.events[-1]["event_type"], "action.awaiting_approval")
        self.assertEqual(result.events[-1]["payload"]["required_decision"], "human_approval")
        self.assertFalse(result.events[-1]["payload"]["effect_performed"])
        self.assertNotIn("approval_granted", canonical_json(list(result.events)))

    def test_explicit_and_unknown_actions_are_denied_without_effect(self) -> None:
        for action_type, reason in (
            ("artifact.delete", "explicit_deny"),
            ("unregistered.action", "unknown_action_default_deny"),
        ):
            worker = FakeWorker()
            result = worker.process_request(action_request(action_type=action_type))
            self.assertEqual(result.events[1]["payload"]["decision"], "deny")
            self.assertEqual(result.events[-1]["event_type"], "action.denied")
            self.assertEqual(result.events[-1]["payload"]["reason_code"], reason)
            self.assertFalse(result.events[-1]["payload"]["effect_performed"])

    def test_same_idempotency_key_replays_receipt_and_changed_payload_conflicts(self) -> None:
        worker = FakeWorker()
        first = worker.process_request(action_request())
        retry = worker.process_request(action_request(request_id="req_retry_2"))

        self.assertTrue(retry.replayed)
        self.assertEqual(retry.events, first.events)
        self.assertEqual(worker.transition_counts["action"], 1)

        replay_id_changed = action_request(
            request_id="req_retry_2",
            idempotency_key="idem_other_after_replay",
        )
        with self.assertRaises(ProtocolError) as replay_conflict:
            worker.process_request(replay_id_changed)
        self.assertEqual(replay_conflict.exception.code, "request_id_conflict")

        changed = action_request(
            request_id="req_changed_3",
            idempotency_key="idem_read_1",
            action_type="research.evidence.read",
        )
        with self.assertRaisesRegex(ProtocolError, "changed") as caught:
            worker.process_request(changed)
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(worker.transition_counts["action"], 1)

    def test_request_id_cannot_change_idempotency_key(self) -> None:
        worker = FakeWorker()
        worker.process_request(action_request())
        changed_key = action_request(idempotency_key="idem_other")

        with self.assertRaises(ProtocolError) as caught:
            worker.process_request(changed_key)
        self.assertEqual(caught.exception.code, "request_id_conflict")

    def test_repeated_cancel_and_resume_replay_without_duplicate_transitions(self) -> None:
        worker = FakeWorker()
        cancel = control_request(
            "cancel",
            request_id="req_cancel_1",
            idempotency_key="idem_cancel_1",
        )
        resume = control_request(
            "resume",
            request_id="req_resume_1",
            idempotency_key="idem_resume_1",
        )

        cancel_first = worker.process_request(cancel)
        cancel_again = worker.process_request(cancel)
        resume_first = worker.process_request(resume)
        resume_again = worker.process_request(resume)

        self.assertTrue(cancel_again.replayed)
        self.assertEqual(cancel_again.events, cancel_first.events)
        self.assertTrue(resume_again.replayed)
        self.assertEqual(resume_again.events, resume_first.events)
        self.assertEqual(worker.transition_counts, {"action": 0, "cancel": 1, "resume": 1})
        self.assertEqual(cancel_first.events[-1]["event_type"], "cancel.accepted")
        self.assertEqual(resume_first.events[-1]["event_type"], "resume.accepted")
        self.assertTrue(all(not event["payload"].get("effect_performed", False) for event in cancel_first.events))
        self.assertTrue(all(not event["payload"].get("effect_performed", False) for event in resume_first.events))

    def test_complete_stream_is_processed_but_partial_stream_is_not(self) -> None:
        worker = FakeWorker()
        raw = encode_message(action_request())
        output = worker.process_stream(raw)

        events = decode_stream(output)
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event["kind"] == "event" for event in events))
        with self.assertRaises(ProtocolError) as caught:
            worker.process_stream(raw.rstrip(b"\n"))
        self.assertEqual(caught.exception.code, "partial_record")


class EventSequenceTests(unittest.TestCase):
    def test_duplicate_event_is_idempotent_but_conflict_and_sequence_reuse_fail(self) -> None:
        store = EventSequenceStore()
        event = event_message(
            event_id="evt_sequence_0",
            request_id="req_sequence_1",
            sequence=0,
            event_type="request.accepted",
            payload={"operation": "cancel"},
        )

        self.assertTrue(store.apply(event).applied)
        duplicate = store.apply(event)
        self.assertFalse(duplicate.applied)
        self.assertTrue(duplicate.duplicate)

        conflict = dict(event)
        conflict["payload"] = {"operation": "resume"}
        with self.assertRaises(ProtocolError) as caught:
            store.apply(conflict)
        self.assertEqual(caught.exception.code, "event_id_conflict")

        reused_sequence = event_message(
            event_id="evt_sequence_other",
            request_id="req_sequence_1",
            sequence=0,
            event_type="request.accepted",
            payload={"operation": "cancel"},
        )
        with self.assertRaises(ProtocolError) as caught:
            store.apply(reused_sequence)
        self.assertEqual(caught.exception.code, "event_out_of_order")

    def test_out_of_order_event_fails_before_application(self) -> None:
        store = EventSequenceStore()
        event = event_message(
            event_id="evt_sequence_2",
            request_id="req_sequence_2",
            sequence=2,
            event_type="request.accepted",
            payload={"operation": "resume"},
        )

        with self.assertRaises(ProtocolError) as caught:
            store.apply(event)
        self.assertEqual(caught.exception.code, "event_out_of_order")


if __name__ == "__main__":
    unittest.main()
