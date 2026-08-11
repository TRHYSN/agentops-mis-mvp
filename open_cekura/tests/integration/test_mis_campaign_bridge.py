from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import server
import open_cekura.mis.persistence as bridge_persistence
from open_cekura.campaigns.runner import CampaignExecution, execute_mock_campaign
from open_cekura.domain.enums import AdapterKind, EvaluationStatus, RunFinalState
from open_cekura.mis.persistence import (
    CallerTransactionRequiredError,
    MISBridgeConflictError,
    persist_campaign_execution,
)
from open_cekura.simulation.mock_agent import MockAgentConfig


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
NOW = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def execution() -> CampaignExecution:
    baseline = asyncio.run(
        execute_mock_campaign(
            suite_path=SCENARIO_SUITE,
            config=MockAgentConfig.baseline(),
            version="baseline",
            campaign_id="occampaign_mis_bridge",
            workspace_id="local-demo",
            created_at=NOW + timedelta(hours=1),
        )
    )
    first = baseline.records[0]
    evaluations = list(first.evaluations)
    evaluations[0] = evaluations[0].model_copy(
        update={
            "status": EvaluationStatus.WARN,
            "score": 0.9,
            "threshold": 0.95,
            "reason_codes": ["bridge_fixture.warn"],
        }
    )
    evaluations[1] = evaluations[1].model_copy(
        update={
            "status": EvaluationStatus.ERROR,
            "score": None,
            "threshold": None,
            "reason_codes": ["bridge_fixture.error"],
        }
    )
    evaluations[2] = evaluations[2].model_copy(
        update={
            "status": EvaluationStatus.SKIPPED,
            "score": None,
            "threshold": None,
            "reason_codes": ["bridge_fixture.skipped"],
        }
    )
    return replace(
        baseline,
        records=(replace(first, evaluations=tuple(evaluations)), *baseline.records[1:]),
    )


@pytest.fixture(scope="module")
def candidate_execution() -> CampaignExecution:
    return asyncio.run(
        execute_mock_campaign(
            suite_path=SCENARIO_SUITE,
            config=MockAgentConfig.candidate(),
            version="candidate",
            campaign_id="occampaign_mis_bridge_candidate",
            workspace_id="local-demo",
            created_at=NOW,
        )
    )


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "agentops_json_array_contains", 2, server.json_array_contains
    )
    connection.create_function(
        "agentops_audit_chain_hash",
        9,
        server.audit_chain_hash_sql,
        deterministic=True,
    )
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(server.SCHEMA_SQL)
    try:
        yield connection
    finally:
        connection.close()


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_bridge_writes_authority_chain_and_vertical_mappings(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
) -> None:
    conn.execute("BEGIN")

    persisted = persist_campaign_execution(
        conn,
        execution,
        workspace_id="local-demo",
    )

    assert conn.in_transaction is True
    assert persisted.campaign_id == execution.campaign.id
    assert persisted.mis_task_id.startswith("tskoc_")
    assert persisted.mis_plan_id.startswith("planoc_")
    assert len(persisted.mis_run_ids) == len(execution.records)
    assert len(persisted.mis_tool_call_ids) == sum(
        len(record.simulation.tool_calls) for record in execution.records
    )
    assert len(persisted.mis_evaluation_ids) == (
        sum(len(record.evaluations) for record in execution.records) - 1
    )
    assert len(persisted.mis_memory_ids) == len(execution.regressions)

    task = conn.execute(
        "SELECT * FROM tasks WHERE task_id=?", (persisted.mis_task_id,)
    ).fetchone()
    plan = conn.execute(
        "SELECT * FROM agent_plans WHERE plan_id=?", (persisted.mis_plan_id,)
    ).fetchone()
    assert task["workspace_id"] == "local-demo"
    assert task["owner_agent_id"] == persisted.mis_agent_id
    agent = conn.execute(
        "SELECT * FROM agents WHERE agent_id=?", (persisted.mis_agent_id,)
    ).fetchone()
    allowed_tools = set(json.loads(agent["allowed_tools"]))
    assert {"lookup_booking", "update_booking"} <= allowed_tools
    assert "create_duplicate_booking" not in allowed_tools
    assert plan["task_id"] == persisted.mis_task_id
    assert plan["verified_at"]
    assert plan["verification_result_hash"]
    assert server.verify_agent_plan_row(plan, conn)["pass"] is True

    assert _count(conn, "runs") == len(execution.records)
    assert _count(conn, "tool_calls") == len(persisted.mis_tool_call_ids)
    assert _count(conn, "evaluations") == len(persisted.mis_evaluation_ids)
    assert _count(conn, "memories") == len(execution.regressions)
    assert conn.execute(
        "SELECT COUNT(*) FROM memories "
        "WHERE memory_type='failure_case' AND review_status='candidate'"
    ).fetchone()[0] == len(execution.regressions)
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_logs "
        "WHERE action='open_cekura.regression_memory.propose'"
    ).fetchone()[0] == len(execution.regressions)
    assert _count(conn, "reliability_regressions") == len(execution.regressions)

    campaign = conn.execute(
        "SELECT * FROM reliability_campaigns WHERE campaign_id=?",
        (execution.campaign.id,),
    ).fetchone()
    assert campaign["mis_task_id"] == persisted.mis_task_id
    assert campaign["mis_plan_id"] == persisted.mis_plan_id
    assert conn.execute(
        "SELECT COUNT(*) FROM reliability_conversation_runs "
        "WHERE mis_run_id IS NOT NULL"
    ).fetchone()[0] == len(execution.records)
    assert conn.execute(
        "SELECT COUNT(*) FROM reliability_observed_tool_calls "
        "WHERE mis_tool_call_id IS NOT NULL"
    ).fetchone()[0] == len(persisted.mis_tool_call_ids)
    assert conn.execute(
        "SELECT COUNT(*) FROM reliability_evaluation_results "
        "WHERE status='skipped' AND mis_evaluation_id IS NULL"
    ).fetchone()[0] == 1

    first_evaluations = execution.records[0].evaluations
    warn = conn.execute(
        "SELECT * FROM evaluations WHERE evaluation_id=?",
        (persisted.mis_evaluation_ids[first_evaluations[0].id],),
    ).fetchone()
    error = conn.execute(
        "SELECT * FROM evaluations WHERE evaluation_id=?",
        (persisted.mis_evaluation_ids[first_evaluations[1].id],),
    ).fetchone()
    assert warn["pass_fail"] == "fail"
    assert '"vertical_status":"warn"' in warn["rubric_json"]
    assert error["score"] == 0.0
    assert error["pass_fail"] == "fail"
    assert '"vertical_status":"error"' in error["rubric_json"]
    assert first_evaluations[2].id not in persisted.mis_evaluation_ids

    assert conn.execute(
        "SELECT COUNT(*) FROM audit_logs "
        "WHERE action='open_cekura.campaign.persist' AND entity_id=?",
        (execution.campaign.id,),
    ).fetchone()[0] == 1


def test_bridge_is_idempotent_and_fails_closed_on_stable_fact_conflict(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
) -> None:
    conn.execute("BEGIN")
    first = persist_campaign_execution(conn, execution, workspace_id="local-demo")
    counts_before = {
        table: _count(conn, table)
        for table in (
            "tasks",
            "agent_plans",
            "runs",
            "tool_calls",
            "evaluations",
            "memories",
            "audit_logs",
            "reliability_campaigns",
        )
    }

    second = persist_campaign_execution(conn, execution, workspace_id="local-demo")

    assert second == first
    assert {
        table: _count(conn, table) for table in counts_before
    } == counts_before

    changed = replace(
        execution,
        agent=execution.agent.model_copy(update={"name": "Conflicting agent facts"}),
    )
    with pytest.raises(MISBridgeConflictError, match="reliability_agents"):
        persist_campaign_execution(conn, changed, workspace_id="local-demo")

    assert {
        table: _count(conn, table) for table in counts_before
    } == counts_before

    conn.execute(
        "UPDATE audit_logs SET metadata_json='{}' "
        "WHERE action='open_cekura.campaign.persist' AND entity_id=?",
        (execution.campaign.id,),
    )
    with pytest.raises(MISBridgeConflictError, match="stable MIS audit"):
        persist_campaign_execution(conn, execution, workspace_id="local-demo")


def test_bridge_requires_a_caller_owned_transaction(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
) -> None:
    with pytest.raises(CallerTransactionRequiredError, match="active transaction"):
        persist_campaign_execution(conn, execution, workspace_id="local-demo")

    assert conn.in_transaction is False
    assert _count(conn, "tasks") == 0

    conn.execute("BEGIN")
    persist_campaign_execution(conn, execution, workspace_id="local-demo")
    conn.rollback()

    assert conn.in_transaction is False
    assert _count(conn, "tasks") == 0
    assert _count(conn, "runs") == 0


def test_bridge_reuses_agent_authority_across_baseline_and_candidate_versions(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    candidate_execution: CampaignExecution,
) -> None:
    conn.execute("BEGIN")

    baseline = persist_campaign_execution(
        conn, execution, workspace_id="local-demo"
    )
    candidate = persist_campaign_execution(
        conn, candidate_execution, workspace_id="local-demo"
    )

    assert candidate.mis_agent_id == baseline.mis_agent_id
    assert candidate.mis_task_id != baseline.mis_task_id
    assert candidate.mis_plan_id != baseline.mis_plan_id
    assert set(candidate.mis_run_ids.values()).isdisjoint(
        baseline.mis_run_ids.values()
    )
    assert _count(conn, "agents") == 1
    assert _count(conn, "tasks") == 2


def test_bridge_rejects_a_rebound_plan_creation_audit(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
) -> None:
    conn.execute("BEGIN")
    persist_campaign_execution(conn, execution, workspace_id="local-demo")
    conn.execute(
        "UPDATE audit_logs SET after_hash='rebound' "
        "WHERE action='agent_gateway.agent_plan_create'"
    )

    with pytest.raises(MISBridgeConflictError, match="stable MIS audit"):
        persist_campaign_execution(conn, execution, workspace_id="local-demo")


@pytest.mark.parametrize("status", ["rejected", "superseded"])
def test_bridge_rejects_a_plan_that_cannot_authorize_run_start(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    status: str,
) -> None:
    conn.execute("BEGIN")
    persisted = persist_campaign_execution(conn, execution, workspace_id="local-demo")
    conn.execute(
        "UPDATE agent_plans SET status=? WHERE plan_id=?",
        (status, persisted.mis_plan_id),
    )

    with pytest.raises(MISBridgeConflictError, match="not executable"):
        persist_campaign_execution(conn, execution, workspace_id="local-demo")


def test_bridge_rejects_approved_plan_with_forged_approval_authority(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
) -> None:
    conn.execute("BEGIN")
    persisted = persist_campaign_execution(conn, execution, workspace_id="local-demo")
    plan = conn.execute(
        "SELECT * FROM agent_plans WHERE plan_id=?", (persisted.mis_plan_id,)
    ).fetchone()
    approval_run_id = server.ensure_agent_plan_approval_run(conn, plan)
    assert approval_run_id
    approval_id = "ap_open_cekura_forged"
    conn.execute(
        """INSERT INTO approvals(
            approval_id,task_id,run_id,tool_call_id,requested_by_agent_id,
            approver_user_id,decision,reason,subject_type,subject_id,subject_hash,
            expires_at,created_at,decided_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            approval_id,
            persisted.mis_task_id,
            approval_run_id,
            None,
            persisted.mis_agent_id,
            None,
            "approved",
            "adversarial fixture",
            "agent_plan",
            "plan_wrong_subject",
            plan["plan_hash"],
            None,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    conn.execute(
        "UPDATE agent_plans SET status='approved', approval_id=? WHERE plan_id=?",
        (approval_id, persisted.mis_plan_id),
    )

    with pytest.raises(MISBridgeConflictError, match="approval authority"):
        persist_campaign_execution(conn, execution, workspace_id="local-demo")


def test_bridge_replays_vertical_rows_with_rfc3339_z_on_python_310(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn.execute("BEGIN")
    first = persist_campaign_execution(conn, execution, workspace_id="local-demo")
    for table in (
        "reliability_agents",
        "reliability_agent_versions",
        "reliability_scenario_suites",
        "reliability_personas",
        "reliability_scenarios",
    ):
        conn.execute(
            f"UPDATE {table} SET created_at=replace(created_at, '+00:00', 'Z')"
        )

    real_datetime = datetime

    class Python310Datetime:
        @classmethod
        def fromisoformat(cls, value: str) -> datetime:
            if value.endswith("Z"):
                raise ValueError("Python 3.10 does not accept an RFC3339 Z suffix")
            return real_datetime.fromisoformat(value)

    monkeypatch.setattr(bridge_persistence, "datetime", Python310Datetime)

    second = persist_campaign_execution(conn, execution, workspace_id="local-demo")

    assert second == first


def test_http_agent_uses_stable_core_harness_and_keeps_http_vertical_runtime(
    conn: sqlite3.Connection,
    candidate_execution: CampaignExecution,
) -> None:
    http_version = candidate_execution.agent_version.model_copy(
        update={"adapter_kind": AdapterKind.HTTP}
    )
    http_execution = replace(
        candidate_execution,
        agent_version=http_version,
        records=tuple(
            replace(
                record,
                simulation=record.simulation.model_copy(
                    update={"agent_version": http_version}
                ),
            )
            for record in candidate_execution.records
        ),
    )
    conn.execute("BEGIN")

    persisted = persist_campaign_execution(
        conn, http_execution, workspace_id="local-demo"
    )

    agent = conn.execute(
        "SELECT * FROM agents WHERE agent_id=?", (persisted.mis_agent_id,)
    ).fetchone()
    assert agent["name"] == "OpenCekura Reliability Harness"
    assert agent["runtime_type"] == "mock"
    assert agent["model_provider"] == "open_cekura"
    assert set(json.loads(agent["allowed_tools"])) == {
        "cancel_booking",
        "list_available_slots",
        "lookup_booking",
        "update_booking",
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM runs WHERE runtime_type='http'"
    ).fetchone()[0] == len(http_execution.records)
    assert conn.execute(
        "SELECT adapter_kind FROM reliability_agent_versions "
        "WHERE agent_version_id=?",
        (http_version.id,),
    ).fetchone()[0] == "http"


def test_core_error_rows_keep_only_stable_classifications(
    conn: sqlite3.Connection,
    candidate_execution: CampaignExecution,
) -> None:
    first = candidate_execution.records[0]
    tool_calls = list(first.simulation.tool_calls)
    assert tool_calls
    tool_calls[0] = tool_calls[0].model_copy(
        update={"error": "TOOL_ERROR_SECRET raw private diagnostic"}
    )
    private_simulation = first.simulation.model_copy(
        update={
            "run": first.simulation.run.model_copy(
                update={"status": RunFinalState.ERROR}
            ),
            "tool_calls": tuple(tool_calls),
            "adapter_error": (
                "Authorization: Bearer ADAPTER_ERROR_SECRET raw private diagnostic"
            ),
        }
    )
    private_execution = replace(
        candidate_execution,
        records=(replace(first, simulation=private_simulation), *candidate_execution.records[1:]),
    )
    conn.execute("BEGIN")

    persisted = persist_campaign_execution(
        conn, private_execution, workspace_id="local-demo"
    )

    run = conn.execute(
        "SELECT error_type,error_message FROM runs WHERE run_id=?",
        (persisted.mis_run_ids[first.simulation.run.id],),
    ).fetchone()
    core_tool = conn.execute(
        "SELECT result_summary FROM tool_calls WHERE tool_call_id=?",
        (persisted.mis_tool_call_ids[tool_calls[0].id],),
    ).fetchone()
    assert run["error_type"] == "open_cekura.adapter_error"
    assert run["error_message"] is None
    assert core_tool["result_summary"] == (
        "OpenCekura observed tool error; raw error omitted."
    )
    core_payload = json.dumps(
        {
            "run": dict(run),
            "tool": dict(core_tool),
            "audits": [
                dict(row)
                for row in conn.execute(
                    "SELECT action,metadata_json FROM audit_logs"
                ).fetchall()
            ],
        }
    )
    assert "ADAPTER_ERROR_SECRET" not in core_payload
    assert "TOOL_ERROR_SECRET" not in core_payload


def test_harness_allowed_tools_are_stable_across_variant_suites(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "variant-suite"
    suite_path.mkdir()
    for index, source in enumerate(sorted(SCENARIO_SUITE.glob("*.yaml"))):
        lines = source.read_text(encoding="utf-8").splitlines()
        for line_index, line in enumerate(lines):
            if line.startswith("id: "):
                lines[line_index] = f"id: variant.{line[4:]}"
                break
        if index == 0:
            confirmation_index = lines.index("  must_confirm_before_mutation: true")
            lines.insert(confirmation_index, "    - variant_forbidden_tool")
        (suite_path / source.name).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    variant = asyncio.run(
        execute_mock_campaign(
            suite_path=suite_path,
            config=MockAgentConfig.candidate(),
            version="variant-suite",
            campaign_id="occampaign_mis_bridge_variant_suite",
            workspace_id="local-demo",
            created_at=execution.campaign.created_at,
        )
    )
    conn.execute("BEGIN")

    baseline = persist_campaign_execution(conn, execution, workspace_id="local-demo")
    variant_mappings = persist_campaign_execution(
        conn, variant, workspace_id="local-demo"
    )

    assert variant_mappings.mis_agent_id == baseline.mis_agent_id
    assert _count(conn, "agents") == 1
    allowed_tools = json.loads(
        conn.execute(
            "SELECT allowed_tools FROM agents WHERE agent_id=?",
            (baseline.mis_agent_id,),
        ).fetchone()[0]
    )
    assert set(allowed_tools) == {
        "cancel_booking",
        "list_available_slots",
        "lookup_booking",
        "update_booking",
    }
    assert "variant_forbidden_tool" not in allowed_tools


@pytest.mark.parametrize(
    "mapping_field",
    ["campaign_task", "campaign_plan", "run", "tool", "evaluation", "regression"],
)
def test_bridge_rejects_conflicting_preexisting_vertical_mappings(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
    mapping_field: str,
) -> None:
    first = execution.records[0]
    changed = execution
    if mapping_field == "campaign_task":
        changed = replace(
            execution,
            campaign=execution.campaign.model_copy(
                update={"mis_task_id": "tsk_prebound_wrong"}
            ),
        )
    elif mapping_field == "campaign_plan":
        changed = replace(
            execution,
            campaign=execution.campaign.model_copy(
                update={"mis_plan_id": "plan_prebound_wrong"}
            ),
        )
    elif mapping_field == "run":
        changed = replace(
            execution,
            records=(
                replace(
                    first,
                    simulation=first.simulation.model_copy(
                        update={
                            "run": first.simulation.run.model_copy(
                                update={"mis_run_id": "run_prebound_wrong"}
                            )
                        }
                    ),
                ),
                *execution.records[1:],
            ),
        )
    elif mapping_field == "tool":
        calls = list(first.simulation.tool_calls)
        calls[0] = calls[0].model_copy(
            update={"mis_tool_call_id": "tcl_prebound_wrong"}
        )
        changed = replace(
            execution,
            records=(
                replace(
                    first,
                    simulation=first.simulation.model_copy(
                        update={"tool_calls": tuple(calls)}
                    ),
                ),
                *execution.records[1:],
            ),
        )
    elif mapping_field == "evaluation":
        evaluations = list(first.evaluations)
        evaluations[0] = evaluations[0].model_copy(
            update={"mis_evaluation_id": "eval_prebound_wrong"}
        )
        changed = replace(
            execution,
            records=(
                replace(first, evaluations=tuple(evaluations)),
                *execution.records[1:],
            ),
        )
    else:
        regressions = list(first.regressions)
        assert regressions
        regressions[0] = regressions[0].model_copy(
            update={"mis_memory_id": "mem_prebound_wrong"}
        )
        changed = replace(
            execution,
            records=(
                replace(first, regressions=tuple(regressions)),
                *execution.records[1:],
            ),
        )
    conn.execute("BEGIN")

    with pytest.raises(MISBridgeConflictError, match="pre-existing MIS mapping"):
        persist_campaign_execution(conn, changed, workspace_id="local-demo")

    assert _count(conn, "tasks") == 0
    assert _count(conn, "runs") == 0


def test_bridge_redacts_core_evaluation_and_memory_payloads(
    conn: sqlite3.Connection,
    execution: CampaignExecution,
) -> None:
    first = execution.records[0]
    evaluations = list(first.evaluations)
    evaluations[0] = evaluations[0].model_copy(
        update={
            "evidence_refs": [
                "Authorization: Bearer EVAL_BEARER_VALUE",
                "cookie=EVAL_COOKIE_VALUE",
            ],
            "metadata": {
                "rawPrompt": "EVAL_RAW_PROMPT_VALUE",
                "safe_reason": "confirmation required",
            },
        }
    )
    regressions = list(first.regressions)
    regressions[0] = regressions[0].model_copy(
        update={
            "original_input": {
                "booking_id": "booking-safe-marker",
                "rawPrompt": "MEMORY_RAW_PROMPT_VALUE",
                "cookieJar": {"session": "MEMORY_COOKIE_VALUE"},
                "access_token": "MEMORY_TOKEN_VALUE",
                "secret": "MEMORY_GENERIC_SECRET_VALUE",
                "token": "MEMORY_GENERIC_TOKEN_VALUE",
            },
            "evidence_refs": ["Bearer MEMORY_BEARER_VALUE"],
        }
    )
    tool_calls = list(first.simulation.tool_calls)
    tool_calls[0] = tool_calls[0].model_copy(
        update={
            "arguments": {
                "booking_id": "booking-safe-marker",
                "authToken": "TOOL_TOKEN_VALUE",
                "cookie": "TOOL_COOKIE_VALUE",
                "rawPrompt": "TOOL_RAW_PROMPT_VALUE",
                "session": "TOOL_SESSION_VALUE",
            }
        }
    )
    private_execution = replace(
        execution,
        records=(
            replace(
                first,
                simulation=first.simulation.model_copy(
                    update={"tool_calls": tuple(tool_calls)}
                ),
                evaluations=tuple(evaluations),
                regressions=tuple(regressions),
            ),
            *execution.records[1:],
        ),
    )
    conn.execute("BEGIN")

    persisted = persist_campaign_execution(
        conn, private_execution, workspace_id="local-demo"
    )

    rubric = conn.execute(
        "SELECT rubric_json FROM evaluations WHERE evaluation_id=?",
        (persisted.mis_evaluation_ids[evaluations[0].id],),
    ).fetchone()[0]
    memory = conn.execute(
        "SELECT canonical_text FROM memories WHERE memory_id=?",
        (persisted.mis_memory_ids[regressions[0].id],),
    ).fetchone()[0]
    tool_args = conn.execute(
        "SELECT normalized_args_json FROM tool_calls WHERE tool_call_id=?",
        (persisted.mis_tool_call_ids[tool_calls[0].id],),
    ).fetchone()[0]
    encoded = rubric + memory + tool_args
    for secret in (
        "EVAL_BEARER_VALUE",
        "EVAL_COOKIE_VALUE",
        "EVAL_RAW_PROMPT_VALUE",
        "MEMORY_RAW_PROMPT_VALUE",
        "MEMORY_COOKIE_VALUE",
        "MEMORY_TOKEN_VALUE",
        "MEMORY_BEARER_VALUE",
        "TOOL_TOKEN_VALUE",
        "TOOL_COOKIE_VALUE",
        "TOOL_RAW_PROMPT_VALUE",
        "MEMORY_GENERIC_SECRET_VALUE",
        "MEMORY_GENERIC_TOKEN_VALUE",
        "TOOL_SESSION_VALUE",
    ):
        assert secret not in encoded
    assert "[REDACTED]" in encoded
    assert "booking-safe-marker" in memory
    assert "confirmation required" not in rubric
