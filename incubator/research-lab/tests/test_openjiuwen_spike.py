from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


SPIKE_ROOT = Path(__file__).resolve().parents[1] / "openjiuwen_spike"
if str(SPIKE_ROOT) not in sys.path:
    sys.path.insert(0, str(SPIKE_ROOT))

from fake_worker import FakeWorker  # noqa: E402
from protocol import (  # noqa: E402
    MAX_COLLECTION_ITEMS,
    MAX_EVENT_IDENTITIES,
    MAX_ID_CHARS,
    MAX_IDEMPOTENCY_RECEIPTS,
    MAX_LINE_BYTES,
    MAX_MESSAGES,
    MAX_PAYLOAD_BYTES,
    MAX_RECEIPT_EVENTS,
    MAX_STRING_CHARS,
    MAX_TOTAL_BYTES,
    SCHEMA_VERSION,
    EventSequenceStore,
    IdempotencyStore,
    PermissionDecision,
    ProtocolError,
    canonical_json,
    classify_permission,
    decode_line,
    decode_stream,
    encode_message,
    event_message,
    receipt_events_for_request,
    stable_protocol_id,
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
        self.assertEqual(
            manifest["openjiuwen"]["notice_files"],
            ["LICENSE", "Open_Source_Software_Notice.txt"],
        )
        self.assertFalse(manifest["execution"]["installed"])
        self.assertFalse(manifest["execution"]["imported"])
        self.assertEqual(manifest["execution"]["real_runtime"], "NOT_RUN")
        self.assertEqual(manifest["unknowns"]["commit_pypi_equivalence"], "UNKNOWN")
        self.assertEqual(manifest["excluded"]["jiuwenswarm"], "NOT_INTEGRATED")
        permission_source = (
            "https://github.com/openJiuwen-ai/agent-core/blob/"
            "bf0a3eb2c70fcbae404403530519ca02e7fc4692/"
            "docs/en/2.Development%20Guide/Tool%20permissions%20and%20host%20integration.md"
        )
        self.assertIn(permission_source, manifest["official_sources"])
        self.assertEqual(
            [source for source in manifest["official_sources"] if "Tool%20permissions" in source],
            [permission_source],
        )
        self.assertIn(
            permission_source,
            (SPIKE_ROOT.parents[2] / "docs" / "OPENJIUWEN_COMPATIBILITY_SPIKE.md").read_text(
                encoding="utf-8"
            ),
        )


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

    def test_noncanonical_wire_encodings_fail_exact_round_trip(self) -> None:
        request = action_request()
        spaced = (json.dumps(request, sort_keys=True) + "\n").encode("utf-8")
        reordered = (
            json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")

        self.assert_protocol_error("noncanonical_encoding", lambda: decode_line(spaced))
        self.assert_protocol_error("noncanonical_encoding", lambda: decode_line(reordered))
        self.assert_protocol_error(
            "partial_record",
            lambda: decode_line(encode_message(request).removesuffix(b"\n")),
        )
        self.assert_protocol_error(
            "multiple_records",
            lambda: decode_line(encode_message(request).removesuffix(b"\n") + b"\r\n"),
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

        for field in (
            "clientApiKey",
            "xApiKey",
            "x-api-key",
            "awsAccessKeyId",
            "aws_access_key_id",
            "passWord",
            "pass-word",
            "pass.word",
            "author-ization",
            "author/ization",
            "cred-ential",
            "cred.ential",
            "passWordHash",
            "pass-word-hash",
            "pass.word.hash",
            "author-ization-header",
            "author/ization/header",
            "apiKeyValue",
            "api-key-value",
            "privateKeyPem",
            "private-key-pem",
            "passwordhash",
            "PASSWORDHASH",
            "authorizationheader",
            "AUTHORIZATIONHEADER",
            "apikeyvalue",
            "APIKEYVALUE",
            "privatekeypem",
            "PRIVATEKEYPEM",
            "clientsecretdigest",
            "accesskeyidvalue",
            "passwordless",
            "authorizationpolicy",
            "xapikeyvalue",
            "awsaccesskeyidvalue",
            "userpasswordhash",
            "dbpasswordhash",
            "sshprivatekeypem",
            "serviceclientsecretdigest",
            "encryptedsecretkeydata",
            "mypasswordless",
            "xauthorizationpolicy",
            "tokenvalue",
            "TOKENDIGEST",
            "cookiedata",
            "COOKIEHEADER",
            "secretdigest",
            "SECRETMATERIAL",
            "rawpromptbody",
            "rawresponsebody",
            "messagesdata",
            "MESSAGESBLOB",
            "transcriptcontent",
            "TRANSCRIPTDATA",
        ):
            request = action_request(arguments={field: "ordinary-looking-value"})
            self.assert_protocol_error("sensitive_key_forbidden", lambda: validate_message(request))

        request = action_request(arguments={"header": "Bearer fixture-value"})
        self.assert_protocol_error("secret_value_forbidden", lambda: validate_message(request))

        for field in (
            "passageWordCount",
            "authorName",
            "credentialingStatus",
            "secretaryName",
            "xApiLatency",
            "accessibilityKeynote",
        ):
            request = action_request(arguments={field: "bounded-benign-value"})
            self.assertEqual(validate_message(request), request)

    def test_unpaired_surrogates_fail_as_protocol_errors(self) -> None:
        request = action_request(arguments={"text": "\ud800"})
        self.assert_protocol_error("invalid_unicode", lambda: validate_message(request))
        self.assert_protocol_error("invalid_unicode", lambda: canonical_json({"text": "\udfff"}))

        valid_wire = encode_message(action_request(arguments={"text": "surrogate-placeholder"}))
        escaped_surrogate_wire = valid_wire.replace(
            b'"surrogate-placeholder"',
            b'"\\ud800"',
        )
        self.assert_protocol_error("invalid_unicode", lambda: decode_line(escaped_surrogate_wire))

        process = subprocess.run(
            [sys.executable, str(SPIKE_ROOT / "fake_worker.py")],
            input=escaped_surrogate_wire,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, b"")
        self.assertLessEqual(len(process.stderr), 256)
        self.assertNotIn(b"Traceback", process.stderr)
        self.assertNotIn(b"ud800", process.stderr)
        self.assertEqual(
            json.loads(process.stderr),
            {"error_code": "invalid_unicode", "input_omitted": True, "ok": False},
        )

    def test_permission_cannot_be_injected_as_approval(self) -> None:
        for field, value in (
            ("approval_granted", True),
            ("reason_code", "explicit_read_only_allow"),
        ):
            request = action_request(action_type="research.remote.submit")
            request["payload"][field] = value
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
        self.assertEqual(
            classify_permission("authority.approve").decision,
            PermissionDecision.DENY,
        )

    def test_permission_event_must_match_classifier_and_reason(self) -> None:
        forged = {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "event_id": "evt_forged_approval",
            "request_id": "req_forged_approval",
            "sequence": 0,
            "event_type": "permission.decision",
            "payload": {
                "action_id": "act_forged_approval",
                "action_type": "authority.approve",
                "decision": "allow",
                "reason_code": "explicit_read_only_allow",
            },
        }
        with self.assertRaises(ProtocolError) as caught:
            validate_message(forged)
        self.assertEqual(caught.exception.code, "permission_classifier_mismatch")

        forged["payload"].update(
            {
                "action_type": "research.remote.submit",
                "decision": "ask",
                "reason_code": "caller_claimed_approval",
            }
        )
        with self.assertRaises(ProtocolError) as caught:
            validate_message(forged)
        self.assertEqual(caught.exception.code, "permission_classifier_mismatch")

        denied = event_message(
            event_id="evt_denied_approval",
            request_id="req_denied_approval",
            sequence=0,
            event_type="permission.decision",
            payload={
                "action_id": "act_denied_approval",
                "action_type": "authority.approve",
                "decision": "deny",
                "reason_code": "explicit_deny",
            },
        )
        self.assertEqual(denied["payload"]["decision"], "deny")

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
            ("authority.approve", "explicit_deny"),
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

    def test_receipt_commit_is_semantically_bound_to_originating_request(self) -> None:
        request = action_request(
            request_id="req_authority_denied",
            idempotency_key="idem_authority_denied",
            action_type="authority.approve",
        )
        expected = receipt_events_for_request(request)
        self.assertEqual(expected[1]["payload"]["decision"], "deny")
        self.assertEqual(expected[2]["event_type"], "action.denied")

        forged_allow = event_message(
            event_id=stable_protocol_id(
                "evt", "req_authority_denied", "1", "permission.decision"
            ),
            request_id="req_authority_denied",
            sequence=1,
            event_type="permission.decision",
            payload={
                "action_id": "act_read_1",
                "action_type": "research.metadata.read",
                "decision": "allow",
                "reason_code": "explicit_read_only_allow",
            },
        )
        forged_terminal = event_message(
            event_id=stable_protocol_id(
                "evt", "req_authority_denied", "2", "action.completed"
            ),
            request_id="req_authority_denied",
            sequence=2,
            event_type="action.completed",
            payload={
                "action_id": "act_read_1",
                "result_summary": "bounded_read_only_receipt",
                "side_effect_performed": False,
            },
        )
        store = IdempotencyStore()
        with self.assertRaises(ProtocolError) as caught:
            store.commit(request, (expected[0], forged_allow, forged_terminal))
        self.assertEqual(caught.exception.code, "receipt_semantic_mismatch")
        self.assertIsNone(store.lookup(request))

        forged_action_id = event_message(
            event_id=expected[1]["event_id"],
            request_id="req_authority_denied",
            sequence=1,
            event_type="permission.decision",
            payload={
                "action_id": "act_other",
                "action_type": "authority.approve",
                "decision": "deny",
                "reason_code": "explicit_deny",
            },
        )
        forged_denied_terminal = event_message(
            event_id=expected[2]["event_id"],
            request_id="req_authority_denied",
            sequence=2,
            event_type="action.denied",
            payload={
                "action_id": "act_other",
                "reason_code": "explicit_deny",
                "effect_performed": False,
            },
        )
        with self.assertRaises(ProtocolError) as caught:
            store.commit(request, (expected[0], forged_action_id, forged_denied_terminal))
        self.assertEqual(caught.exception.code, "receipt_semantic_mismatch")
        self.assertIsNone(store.lookup(request))

        deny_then_forged_terminal = (expected[0], expected[1], forged_terminal)
        with self.assertRaises(ProtocolError) as caught:
            store.commit(request, deny_then_forged_terminal)
        self.assertEqual(caught.exception.code, "receipt_semantic_mismatch")
        self.assertIsNone(store.lookup(request))

        committed = store.commit(request, expected)
        self.assertEqual(committed.events, expected)

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

    def test_idempotency_and_sequence_stores_fail_closed_at_hard_capacity(self) -> None:
        receipts = IdempotencyStore(max_receipts=1, max_request_ids=1)
        first_request = action_request()
        self.assertIsNone(receipts.lookup(first_request))
        receipts.commit(first_request, receipt_events_for_request(first_request))
        self.assertIsNotNone(receipts.lookup(first_request))

        second_request = action_request(
            request_id="req_capacity_2",
            idempotency_key="idem_capacity_2",
        )
        with self.assertRaises(ProtocolError) as caught:
            receipts.lookup(second_request)
        self.assertEqual(caught.exception.code, "store_capacity_exceeded")

        events = EventSequenceStore(max_events=1, max_requests=1)
        first_event = event_message(
            event_id="evt_capacity_1",
            request_id="req_capacity_1",
            sequence=0,
            event_type="request.accepted",
            payload={"operation": "cancel"},
        )
        events.apply(first_event)
        self.assertTrue(events.apply(first_event).duplicate)
        second_event = event_message(
            event_id="evt_capacity_2",
            request_id="req_capacity_1",
            sequence=1,
            event_type="cancel.requested",
            payload={"target_request_id": "req_target_1", "effect_performed": False},
        )
        with self.assertRaises(ProtocolError) as caught:
            events.apply(second_event)
        self.assertEqual(caught.exception.code, "store_capacity_exceeded")

        with self.assertRaises(ProtocolError) as caught:
            IdempotencyStore(max_receipts=MAX_IDEMPOTENCY_RECEIPTS + 1)
        self.assertEqual(caught.exception.code, "invalid_store_limit")
        with self.assertRaises(ProtocolError) as caught:
            EventSequenceStore(max_events=MAX_EVENT_IDENTITIES + 1)
        self.assertEqual(caught.exception.code, "invalid_store_limit")

        bounded_receipts = IdempotencyStore(max_receipts=1, max_request_ids=1)
        too_many_events = tuple(
            event_message(
                event_id=f"evt_receipt_{index}",
                request_id="req_read_1",
                sequence=index,
                event_type="request.accepted",
                payload={"operation": "action.propose"},
            )
            for index in range(MAX_RECEIPT_EVENTS + 1)
        )
        with self.assertRaises(ProtocolError) as caught:
            bounded_receipts.commit(first_request, too_many_events)
        self.assertEqual(caught.exception.code, "receipt_too_large")


if __name__ == "__main__":
    unittest.main()
