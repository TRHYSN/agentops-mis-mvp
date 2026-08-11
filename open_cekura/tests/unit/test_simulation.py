from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from typing import Any, Iterator

import pytest

from open_cekura.domain.enums import AdapterKind, RunFinalState, TurnRole
from open_cekura.domain.models import (
    AgentVersion,
    ConversationRun,
    ConversationTurn,
    ObservedToolCall,
)
from open_cekura.scenarios.schema import ScenarioDefinition
from open_cekura.scenarios.loader import load_suite


CREATED_AT = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)
INITIAL_SLOT = "2026-08-20T09:00:00Z"
REQUESTED_SLOT = "2026-08-22T15:00:00Z"
PUBLIC_SCENARIOS = Path(__file__).resolve().parents[3] / "examples" / "open-cekura" / "scenarios"
PUBLIC_AGENT_CONFIGS = (
    Path(__file__).resolve().parents[3]
    / "examples"
    / "open-cekura"
    / "appointment-agent"
)


def simulation_modules():
    adapter = import_module("open_cekura.simulation.agent_adapter")
    mock = import_module("open_cekura.simulation.mock_agent")
    http = import_module("open_cekura.simulation.http_agent")
    runner = import_module("open_cekura.simulation.runner")
    return adapter, mock, http, runner


def make_scenario(
    *,
    scenario_id: str = "appointment.change_after_interrupt",
    challenges: list[dict[str, Any]] | None = None,
    required_tool_calls: list[str] | None = None,
    must_confirm: bool = True,
    final_state: dict[str, Any] | None = None,
) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "schema_version": 1,
            "id": scenario_id,
            "name": "Appointment reliability scenario",
            "persona": {
                "language": "en-US",
                "tone": "impatient",
                "verbosity": "short",
            },
            "initial_message": "Move booking booking-001 to Saturday afternoon.",
            "goal": {
                "type": "reschedule",
                "booking_id": "booking-001",
                "requested_slot": REQUESTED_SLOT,
            },
            "challenges": challenges or [],
            "expectations": {
                "required_tool_calls": required_tool_calls
                or ["lookup_booking", "update_booking"],
                "forbidden_tool_calls": ["create_duplicate_booking"],
                "must_confirm_before_mutation": must_confirm,
                "final_state": final_state
                or {
                    "booking_id": "booking-001",
                    "slot": REQUESTED_SLOT,
                    "status": "confirmed",
                    "booking_updated": True,
                },
                "max_turns": 20,
                "timeout_ms": 500,
            },
        }
    )


def make_version(
    config: Any,
    *,
    version: str = "candidate",
    adapter_kind: AdapterKind = AdapterKind.MOCK,
) -> AgentVersion:
    return AgentVersion(
        schema_version=1,
        id=f"ocagentv_{version}",
        agent_id="ocagent_appointment",
        version=version,
        adapter_kind=adapter_kind,
        config_sha256=config.canonical_sha256(),
        created_at=CREATED_AT,
    )


def run_mock(
    scenario: ScenarioDefinition,
    config: Any,
    *,
    campaign_id: str = "occampaign_candidate",
    version: str = "candidate",
):
    _, mock, _, runner = simulation_modules()
    return asyncio.run(
        runner.run_scenario(
            scenario=scenario,
            agent_version=make_version(config, version=version),
            adapter=mock.MockAgentAdapter(config),
            campaign_id=campaign_id,
            created_at=CREATED_AT,
        )
    )


def normalized_behavior(result: Any) -> dict[str, Any]:
    return {
        "run_status": result.run.status.value,
        "turns": [(turn.role.value, turn.content) for turn in result.turns],
        "tool_calls": [
            {
                "name": call.name,
                "arguments": call.arguments,
                "result": call.result,
                "error": call.error,
                "is_mutation": call.is_mutation,
                "duration_ms": call.duration_ms,
            }
            for call in result.tool_calls
        ],
        "initial_state": result.initial_state,
        "final_state": result.final_state,
        "timed_out": result.timed_out,
        "adapter_error": result.adapter_error,
        "agent_success_claim": result.agent_success_claim,
    }


def test_agent_adapter_declares_the_frozen_async_lifecycle() -> None:
    adapter, _, _, _ = simulation_modules()

    assert inspect.isabstract(adapter.AgentAdapter)
    for method_name in ("start", "send", "observe_tool_calls", "close"):
        method = getattr(adapter.AgentAdapter, method_name)
        assert getattr(method, "__isabstractmethod__", False)
        assert inspect.iscoroutinefunction(method)


def test_candidate_run_is_typed_ordered_and_deterministic() -> None:
    _, mock, _, _ = simulation_modules()
    scenario = make_scenario(
        challenges=[
            {"interrupt_after_turn": 1},
            {"change_constraint_after_turn": 2},
        ]
    )
    config = mock.MockAgentConfig.candidate()

    first = run_mock(scenario, config)
    second = run_mock(scenario, config)

    assert isinstance(first.run, ConversationRun)
    assert all(isinstance(turn, ConversationTurn) for turn in first.turns)
    assert all(isinstance(call, ObservedToolCall) for call in first.tool_calls)
    assert tuple(turn.turn_index for turn in first.turns) == tuple(range(len(first.turns)))
    assert [call.name for call in first.tool_calls] == [
        "lookup_booking",
        "update_booking",
    ]
    assert first.run.status is RunFinalState.PASS
    assert first.agent_success_claim is True
    assert first.final_state == scenario.expectations.final_state
    assert first.scenario == scenario
    assert first.agent_version.config_sha256 == config.canonical_sha256()
    assert first.duration_ms == sum(call.duration_ms for call in first.tool_calls)
    assert first.finished_at - first.started_at == timedelta(
        milliseconds=first.duration_ms
    )
    assert any(
        "[interruption]" in turn.content
        for turn in first.turns
        if turn.role is TurnRole.USER
    )
    assert any(
        "[constraint-change]" in turn.content
        for turn in first.turns
        if turn.role is TurnRole.USER
    )
    assert first.canonical_json_bytes() == second.canonical_json_bytes()


def test_after_turn_challenges_wait_for_the_earliest_legal_user_boundary() -> None:
    _, mock, _, _ = simulation_modules()
    scenario = make_scenario(
        challenges=[
            {"interrupt_after_turn": 3},
            {"change_constraint_after_turn": 4},
        ]
    )

    result = run_mock(scenario, mock.MockAgentConfig.candidate())
    user_turns = [turn for turn in result.turns if turn.role is TurnRole.USER]
    interruption = next(
        turn for turn in user_turns if "[interruption]" in turn.content
    )
    constraint_change = next(
        turn for turn in user_turns if "[constraint-change]" in turn.content
    )

    assert "[continuation]" in user_turns[1].content
    assert interruption.turn_index == 4
    assert constraint_change.turn_index == 6
    assert interruption.turn_index >= 3
    assert constraint_change.turn_index >= 4
    assert result.run.status is RunFinalState.PASS


def test_equal_after_turn_thresholds_keep_scenario_order() -> None:
    _, mock, _, _ = simulation_modules()
    scenario = make_scenario(
        challenges=[
            {"change_constraint_after_turn": 3},
            {"interrupt_after_turn": 3},
        ]
    )

    result = run_mock(scenario, mock.MockAgentConfig.candidate())
    challenge_turns = [
        turn
        for turn in result.turns
        if turn.role is TurnRole.USER
        and (
            "[constraint-change]" in turn.content
            or "[interruption]" in turn.content
        )
    ]

    assert [turn.turn_index for turn in challenge_turns] == [4, 6]
    assert "[constraint-change]" in challenge_turns[0].content
    assert "[interruption]" in challenge_turns[1].content


def test_candidate_final_state_matches_all_public_appointment_fixtures() -> None:
    _, mock, _, _ = simulation_modules()
    config = mock.MockAgentConfig.candidate()

    for scenario in load_suite(PUBLIC_SCENARIOS):
        result = run_mock(
            scenario,
            config,
            campaign_id="occampaign_public_candidate",
        )
        assert result.final_state == scenario.expectations.final_state, scenario.id


def test_defects_are_config_and_challenge_driven_not_id_driven() -> None:
    _, mock, _, _ = simulation_modules()
    scenario = make_scenario(
        challenges=[
            {"duplicate_request_after_turn": 2},
            {"mutation_before_confirmation": True},
        ]
    )
    baseline = mock.MockAgentConfig(
        duplicate_mutations=True,
        mutate_before_confirmation=True,
        claim_failure_after_success=False,
        claim_success_without_mutation=False,
        call_forbidden_tools=False,
    )
    candidate = mock.MockAgentConfig.candidate()

    baseline_result = run_mock(
        scenario,
        baseline,
        campaign_id="occampaign_one",
        version="baseline",
    )
    same_behavior_different_ids = run_mock(
        scenario.model_copy(update={"id": "appointment.same_contract_other_id"}),
        baseline,
        campaign_id="occampaign_two",
        version="baseline-copy",
    )
    candidate_result = run_mock(scenario, candidate)

    baseline_updates = [
        call for call in baseline_result.tool_calls if call.name == "update_booking"
    ]
    candidate_updates = [
        call for call in candidate_result.tool_calls if call.name == "update_booking"
    ]
    assert len(baseline_updates) == 2
    assert len(candidate_updates) == 1

    baseline_turn_index = {turn.id: turn.turn_index for turn in baseline_result.turns}
    candidate_turn_index = {turn.id: turn.turn_index for turn in candidate_result.turns}
    baseline_confirm = next(
        turn.turn_index
        for turn in baseline_result.turns
        if turn.role is TurnRole.USER and "[confirmation]" in turn.content
    )
    candidate_confirm = next(
        turn.turn_index
        for turn in candidate_result.turns
        if turn.role is TurnRole.USER and "[confirmation]" in turn.content
    )
    assert baseline_turn_index[baseline_updates[0].turn_id] < baseline_confirm
    assert candidate_turn_index[candidate_updates[0].turn_id] > candidate_confirm
    assert normalized_behavior(baseline_result) == normalized_behavior(
        same_behavior_different_ids
    )


def test_mock_config_hash_must_match_agent_version() -> None:
    adapter, mock, _, _ = simulation_modules()
    scenario = make_scenario()
    candidate = mock.MockAgentConfig.candidate()
    baseline = mock.MockAgentConfig.baseline()

    async def start_with_mismatch() -> None:
        instance = mock.MockAgentAdapter(baseline)
        await instance.start(scenario, make_version(candidate))

    with pytest.raises(adapter.AdapterContractError, match="config_sha256"):
        asyncio.run(start_with_mismatch())


def test_baseline_profile_contains_exactly_three_explicit_defect_classes() -> None:
    _, mock, _, _ = simulation_modules()

    assert mock.MockAgentConfig.baseline().model_dump(mode="json") == {
        "schema_version": 1,
        "duplicate_mutations": True,
        "mutate_before_confirmation": True,
        "claim_failure_after_success": False,
        "claim_success_without_mutation": True,
        "call_forbidden_tools": False,
    }


@pytest.mark.parametrize(
    ("filename", "factory_name"),
    [("baseline.json", "baseline"), ("candidate.json", "candidate")],
)
def test_public_agent_version_configs_match_executable_mock_profiles(
    filename: str,
    factory_name: str,
) -> None:
    _, mock, _, _ = simulation_modules()
    payload = json.loads((PUBLIC_AGENT_CONFIGS / filename).read_text(encoding="utf-8"))

    config = mock.MockAgentConfig.model_validate(payload)

    assert config == getattr(mock.MockAgentConfig, factory_name)()


def test_mock_timeout_is_evidence_bearing() -> None:
    _, mock, _, _ = simulation_modules()
    scenario = make_scenario(
        challenges=[
            {"backend_timeout_on_tool": "update_booking", "timeout_ms": 25}
        ],
        final_state={
            "booking_id": "booking-001",
            "slot": INITIAL_SLOT,
            "status": "confirmed",
            "booking_updated": False,
        },
    )

    result = run_mock(scenario, mock.MockAgentConfig.candidate())
    timed_out_call = next(
        call for call in result.tool_calls if call.name == "update_booking"
    )

    assert result.timed_out is True
    assert result.adapter_error == "tool_timeout:update_booking"
    assert result.run.status is RunFinalState.ERROR
    assert timed_out_call.error == "timeout"
    assert timed_out_call.duration_ms == 25
    assert result.final_state["booking_updated"] is False


@pytest.mark.parametrize(
    ("challenge", "flag", "expected_claim", "expected_updated", "expected_status"),
    [
        (
            {"tool_succeeds_agent_claims_failure": "update_booking"},
            "claim_failure_after_success",
            False,
            True,
            RunFinalState.FAIL,
        ),
        (
            {"agent_claims_success_without_mutation": True},
            "claim_success_without_mutation",
            True,
            False,
            RunFinalState.PASS,
        ),
    ],
)
def test_mock_claim_defects_preserve_observed_reality(
    challenge: dict[str, Any],
    flag: str,
    expected_claim: bool,
    expected_updated: bool,
    expected_status: RunFinalState,
) -> None:
    _, mock, _, _ = simulation_modules()
    values = {
        "duplicate_mutations": False,
        "mutate_before_confirmation": False,
        "claim_failure_after_success": False,
        "claim_success_without_mutation": False,
        "call_forbidden_tools": False,
    }
    values[flag] = True
    config = mock.MockAgentConfig(**values)
    scenario = make_scenario(challenges=[challenge])

    result = run_mock(scenario, config, version=flag)

    assert result.agent_success_claim is expected_claim
    assert result.final_state["booking_updated"] is expected_updated
    assert result.run.status is expected_status


def test_mock_adapter_has_no_network_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    _, mock, _, _ = simulation_modules()

    def forbidden_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("MockAgentAdapter attempted network I/O")

    monkeypatch.setattr("socket.create_connection", forbidden_network)
    result = run_mock(make_scenario(), mock.MockAgentConfig.candidate())

    assert result.run.status is RunFinalState.PASS


@contextmanager
def local_agent_endpoint(
    *,
    malformed_operation: str | None = None,
    operation_delay_seconds: float = 0.0,
    send_delay_seconds: float = 0.0,
) -> Iterator[tuple[str, list[tuple[str, dict[str, Any]]]]]:
    requests: list[tuple[str, dict[str, Any]]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def _reply(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _delay(self) -> None:
            if operation_delay_seconds:
                time.sleep(operation_delay_seconds)

        def do_POST(self) -> None:
            payload = self._read_payload()
            requests.append((self.path, payload))
            if self.path == "/start":
                self._delay()
                if malformed_operation == "start":
                    self._reply({"schema_version": 1, "accepted": "yes"})
                else:
                    self._reply({"schema_version": 1, "accepted": True})
            elif self.path == "/send":
                self._delay()
                if send_delay_seconds:
                    time.sleep(send_delay_seconds)
                if malformed_operation == "send":
                    self._reply(
                        {
                            "schema_version": 1,
                            "message": "Invalid extra field",
                            "completed": True,
                            "success_claim": True,
                            "final_state": {},
                            "timed_out": False,
                            "unexpected": "must fail",
                        }
                    )
                else:
                    self._reply(
                        {
                            "schema_version": 1,
                            "message": "Appointment updated.",
                            "completed": True,
                            "success_claim": True,
                            "final_state": {
                                "booking_id": "booking-001",
                                "slot": REQUESTED_SLOT,
                                "status": "confirmed",
                                "booking_updated": True,
                            },
                            "timed_out": False,
                            "error": None,
                        }
                    )
            elif self.path == "/close":
                self._delay()
                if malformed_operation == "close":
                    self._reply({"schema_version": 1, "accepted": "yes"})
                else:
                    self._reply({"schema_version": 1, "accepted": True})
            else:
                self.send_error(404)

        def do_GET(self) -> None:
            requests.append((self.path, {}))
            if self.path == "/tool-calls":
                self._delay()
                if malformed_operation == "tool-calls":
                    self._reply({"schema_version": 1, "tool_calls": "invalid"})
                else:
                    self._reply(
                        {
                            "schema_version": 1,
                            "tool_calls": [
                                {
                                    "schema_version": 1,
                                    "name": "update_booking",
                                    "arguments": {
                                        "booking_id": "booking-001",
                                        "slot": REQUESTED_SLOT,
                                    },
                                    "result": {"booking_updated": True},
                                    "error": None,
                                    "is_mutation": True,
                                    "duration_ms": 8,
                                }
                            ],
                        }
                    )
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_adapter_uses_strict_versioned_stdlib_contract() -> None:
    _, mock, http, _ = simulation_modules()
    scenario = make_scenario()
    config = mock.MockAgentConfig.candidate()
    version = make_version(config, adapter_kind=AdapterKind.HTTP)

    with local_agent_endpoint() as (base_url, requests):
        instance = http.HTTPAgentAdapter(base_url, timeout_seconds=0.5)

        async def exercise() -> tuple[Any, Any]:
            await instance.start(scenario, version)
            reply = await instance.send(scenario.initial_message)
            calls = await instance.observe_tool_calls()
            await instance.close()
            return reply, calls

        reply, calls = asyncio.run(exercise())

    assert reply.success_claim is True
    assert calls[0].name == "update_booking"
    assert [path for path, _ in requests] == [
        "/start",
        "/send",
        "/tool-calls",
        "/close",
    ]
    assert requests[0][1]["scenario"]["id"] == scenario.id
    assert requests[0][1]["agent_version"]["config_sha256"] == version.config_sha256


def test_http_adapter_rejects_a_non_http_agent_version_before_network() -> None:
    adapter, mock, http, _ = simulation_modules()
    scenario = make_scenario()
    config = mock.MockAgentConfig.candidate()

    with local_agent_endpoint() as (base_url, requests):
        instance = http.HTTPAgentAdapter(base_url, timeout_seconds=0.5)
        with pytest.raises(adapter.AdapterContractError, match="adapter_kind=http"):
            asyncio.run(instance.start(scenario, make_version(config)))

    assert requests == []


def test_http_adapter_surfaces_response_contract_errors() -> None:
    adapter, mock, http, _ = simulation_modules()
    scenario = make_scenario()
    config = mock.MockAgentConfig.candidate()
    version = make_version(config, adapter_kind=AdapterKind.HTTP)

    with local_agent_endpoint(malformed_operation="send") as (base_url, _):
        instance = http.HTTPAgentAdapter(base_url, timeout_seconds=0.5)

        async def exercise() -> None:
            await instance.start(scenario, version)
            try:
                await instance.send(scenario.initial_message)
            finally:
                await instance.close()

        with pytest.raises(adapter.AdapterContractError, match="send response"):
            asyncio.run(exercise())


@pytest.mark.parametrize(
    ("operation", "stage"),
    [
        ("start", "start"),
        ("send", "send"),
        ("tool-calls", "observe_tool_calls"),
        ("close", "close"),
    ],
)
def test_run_scenario_persists_http_contract_errors(
    operation: str,
    stage: str,
) -> None:
    _, mock, http, runner = simulation_modules()
    scenario = make_scenario()
    config = mock.MockAgentConfig.candidate()
    version = make_version(config, adapter_kind=AdapterKind.HTTP)

    with local_agent_endpoint(malformed_operation=operation) as (base_url, _):
        result = asyncio.run(
            runner.run_scenario(
                scenario=scenario,
                agent_version=version,
                adapter=http.HTTPAgentAdapter(base_url, timeout_seconds=0.5),
                campaign_id="occampaign_http_contract_error",
                created_at=CREATED_AT,
            )
        )

    assert result.run.status is RunFinalState.ERROR
    assert result.adapter_error is not None
    assert result.adapter_error.startswith(f"adapter_contract:{stage}:")


def test_http_run_timing_includes_full_adapter_lifecycle_wall_latency() -> None:
    _, mock, http, runner = simulation_modules()
    scenario = make_scenario()
    config = mock.MockAgentConfig.candidate()
    version = make_version(config, adapter_kind=AdapterKind.HTTP)

    with local_agent_endpoint(operation_delay_seconds=0.03) as (base_url, _):
        result = asyncio.run(
            runner.run_scenario(
                scenario=scenario,
                agent_version=version,
                adapter=http.HTTPAgentAdapter(base_url, timeout_seconds=0.5),
                campaign_id="occampaign_http_wall_latency",
                created_at=CREATED_AT,
            )
        )

    measured_delta_ms = int(
        (result.finished_at - result.started_at).total_seconds() * 1000
    )
    assert result.run.status is RunFinalState.PASS
    assert result.duration_ms >= 100
    assert measured_delta_ms == result.duration_ms


def test_http_adapter_surfaces_explicit_timeout() -> None:
    adapter, mock, http, _ = simulation_modules()
    scenario = make_scenario()
    config = mock.MockAgentConfig.candidate()
    version = make_version(config, adapter_kind=AdapterKind.HTTP)

    with local_agent_endpoint(send_delay_seconds=0.15) as (base_url, _):
        instance = http.HTTPAgentAdapter(base_url, timeout_seconds=0.02)

        async def exercise() -> None:
            await instance.start(scenario, version)
            await instance.send(scenario.initial_message)

        with pytest.raises(adapter.AdapterTimeoutError, match="timed out"):
            asyncio.run(exercise())
