from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from open_cekura.domain.enums import (
    AdapterKind,
    CampaignStatus,
    EvaluationStatus,
    GateDecision,
    RunFinalState,
    TurnRole,
    Verbosity,
)
from open_cekura.domain.models import (
    AgentUnderTest,
    AgentVersion,
    Campaign,
    ConversationRun,
    ConversationTurn,
    EvaluationResult,
    EvidenceEnvironment,
    EvidenceManifest,
    FailureCase,
    FailureCluster,
    ObservedToolCall,
    Persona,
    RegressionCase,
    ReleaseGateDecision,
    Scenario,
    ScenarioSuite,
)
from open_cekura.scenarios.schema import ScenarioDefinition


NOW = datetime(2026, 8, 11, 12, 30, tzinfo=timezone.utc)
SHA = "a" * 64


def storage_modules():
    repository = import_module("open_cekura.storage.repository")
    sqlite_repository = import_module("open_cekura.storage.sqlite_repository")
    return repository, sqlite_repository


AUTHORITY_SCHEMA = """
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL
);
CREATE TABLE agent_plans (
    plan_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_plan_id TEXT,
    agent_id TEXT NOT NULL
);
CREATE TABLE tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    agent_id TEXT NOT NULL
);
CREATE TABLE evaluations (
    evaluation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    score REAL NOT NULL,
    pass_fail TEXT NOT NULL,
    rubric_json TEXT NOT NULL
);
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT,
    run_id TEXT,
    artifact_type TEXT NOT NULL,
    content_hash TEXT
);
CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    subject_type TEXT,
    subject_id TEXT
);
CREATE TABLE memories (
    memory_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    task_id TEXT,
    source_ref TEXT,
    review_status TEXT NOT NULL
);
CREATE TABLE plan_evidence_manifests (
    manifest_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    task_id TEXT,
    run_id TEXT NOT NULL,
    tool_call_ids_json TEXT NOT NULL,
    evaluation_ids_json TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


def open_database(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(AUTHORITY_SCHEMA)
    conn.executemany(
        "INSERT INTO tasks(task_id,workspace_id) VALUES(?,?)",
        [("tsk_a", "ws-a"), ("tsk_b", "ws-b")],
    )
    conn.executemany(
        "INSERT INTO agent_plans(plan_id,workspace_id,task_id) VALUES(?,?,?)",
        [("plan_a", "ws-a", "tsk_a"), ("plan_b", "ws-b", "tsk_b")],
    )
    conn.executemany(
        """INSERT INTO runs(
            run_id,workspace_id,task_id,agent_plan_id,agent_id
        ) VALUES(?,?,?,?,?)""",
        [
            ("run_a", "ws-a", "tsk_a", "plan_a", "agt_a"),
            ("run_a_rollback", "ws-a", "tsk_a", "plan_a", "agt_a"),
            ("run_b", "ws-b", "tsk_b", "plan_b", "agt_b"),
        ],
    )
    conn.executemany(
        """INSERT INTO tool_calls(
            tool_call_id,run_id,tool_name,agent_id
        ) VALUES(?,?,?,?)""",
        [
            ("tc_a", "run_a", "update_booking", "agt_a"),
            ("tc_b", "run_b", "update_booking", "agt_b"),
        ],
    )
    conn.executemany(
        """INSERT INTO evaluations(
            evaluation_id,run_id,task_id,agent_id,score,pass_fail,rubric_json
        ) VALUES(?,?,?,?,?,?,?)""",
        [
            (
                "eval_a",
                "run_a",
                "tsk_a",
                "agt_a",
                0.0,
                "fail",
                '{"evaluator_id":"confirmation_before_mutation.v1","vertical_status":"fail"}',
            ),
            (
                "eval_b",
                "run_b",
                "tsk_b",
                "agt_b",
                0.0,
                "fail",
                '{"evaluator_id":"confirmation_before_mutation.v1","vertical_status":"fail"}',
            ),
        ],
    )
    conn.executemany(
        """INSERT INTO artifacts(
            artifact_id,task_id,run_id,artifact_type,content_hash
        ) VALUES(?,?,?,?,?)""",
        [
            ("art_a", "tsk_a", "run_a", "evidence", SHA),
            ("art_b", "tsk_b", "run_b", "evidence", SHA),
        ],
    )
    conn.executemany(
        """INSERT INTO approvals(
            approval_id,task_id,run_id,decision,subject_type,subject_id
        ) VALUES(?,?,?,?,?,?)""",
        [
            (
                "ap_a",
                "tsk_a",
                "run_a",
                "approved",
                "reliability_release_gate",
                "ocgate_candidate",
            ),
            (
                "ap_b",
                "tsk_b",
                "run_b",
                "approved",
                "reliability_release_gate",
                "ocgate_candidate",
            ),
        ],
    )
    conn.executemany(
        """INSERT INTO memories(
            memory_id,workspace_id,memory_type,task_id,source_ref,review_status
        ) VALUES(?,?,?,?,?,?)""",
        [
            ("mem_a", "ws-a", "failure_case", "tsk_a", "run_a", "candidate"),
            ("mem_b", "ws-b", "failure_case", "tsk_b", "run_b", "candidate"),
        ],
    )
    conn.executemany(
        """INSERT INTO plan_evidence_manifests(
            manifest_id,workspace_id,plan_id,task_id,run_id,tool_call_ids_json,
            evaluation_ids_json,artifact_ids_json,status
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (
                "pem_a",
                "ws-a",
                "plan_a",
                "tsk_a",
                "run_a",
                '["tc_a"]',
                '["eval_a"]',
                '["art_a"]',
                "verified",
            ),
            (
                "pem_b",
                "ws-b",
                "plan_b",
                "tsk_b",
                "run_b",
                '["tc_b"]',
                '["eval_b"]',
                '["art_b"]',
                "verified",
            ),
        ],
    )
    conn.commit()
    return conn


def open_actual_mis_database(path: Path) -> sqlite3.Connection:
    from server import SCHEMA_SQL

    timestamp = NOW.isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        """INSERT INTO agents(
            agent_id,name,role,runtime_type,status,permission_level,allowed_tools,
            created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "agt_a",
            "Appointment agent",
            "reliability target",
            "mock",
            "idle",
            "standard",
            "[]",
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO tasks(
            task_id,workspace_id,title,status,risk_level,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?)""",
        (
            "tsk_a",
            "ws-a",
            "Reliability campaign",
            "completed",
            "low",
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO agent_plans(
            plan_id,workspace_id,task_id,agent_id,task_understanding,risk_level,
            approval_required,status,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "plan_a",
            "ws-a",
            "tsk_a",
            "agt_a",
            "Run the deterministic appointment reliability suite.",
            "low",
            0,
            "approved",
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO runs(
            run_id,workspace_id,task_id,agent_id,runtime_type,status,started_at,
            ended_at,agent_plan_id,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "run_a",
            "ws-a",
            "tsk_a",
            "agt_a",
            "mock",
            "completed",
            timestamp,
            timestamp,
            "plan_a",
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO tool_calls(
            tool_call_id,run_id,agent_id,tool_name,tool_category,
            normalized_args_json,risk_level,status,started_at,ended_at,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "tc_a",
            "run_a",
            "agt_a",
            "update_booking",
            "custom",
            '{"booking_id":"booking-001"}',
            "low",
            "completed",
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO evaluations(
            evaluation_id,task_id,run_id,agent_id,evaluator_type,score,pass_fail,
            rubric_json,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            "eval_a",
            "tsk_a",
            "run_a",
            "agt_a",
            "rule",
            0.0,
            "fail",
            '{"evaluator_id":"confirmation_before_mutation.v1","vertical_status":"fail"}',
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO artifacts(
            artifact_id,task_id,run_id,artifact_type,title,uri,content_hash,created_at
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            "art_a",
            "tsk_a",
            "run_a",
            "evidence",
            "OpenCekura evidence",
            "artifacts/open-cekura/run_a/evidence_manifest.json",
            SHA,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO approvals(
            approval_id,task_id,run_id,requested_by_agent_id,decision,reason,
            subject_type,subject_id,created_at,decided_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "ap_a",
            "tsk_a",
            "run_a",
            "agt_a",
            "approved",
            "Reliability gate passed.",
            "reliability_release_gate",
            "ocgate_candidate",
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO memories(
            memory_id,workspace_id,scope,memory_type,canonical_text,source_type,
            source_ref,task_id,agent_id,review_status,access_tags,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "mem_a",
            "ws-a",
            "task",
            "failure_case",
            "Confirm before mutation.",
            "run_log",
            "run_a",
            "tsk_a",
            "agt_a",
            "candidate",
            "[]",
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """INSERT INTO plan_evidence_manifests(
            manifest_id,workspace_id,plan_id,task_id,run_id,agent_id,mismatch_policy,
            expected_steps_json,tool_call_ids_json,evaluation_ids_json,artifact_ids_json,
            audit_ids_json,status,verification_json,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "pem_a",
            "ws-a",
            "plan_a",
            "tsk_a",
            "run_a",
            "agt_a",
            "block",
            "[]",
            '["tc_a"]',
            '["eval_a"]',
            '["art_a"]',
            "[]",
            "verified",
            '{"status":"verified"}',
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return conn


def scenario_contract() -> ScenarioDefinition:
    return ScenarioDefinition.model_validate(
        {
            "schema_version": 1,
            "id": "appointment.basic",
            "name": "Basic appointment",
            "persona": {
                "language": "en-US",
                "tone": "calm",
                "verbosity": "short",
            },
            "initial_message": "Move my appointment.",
            "goal": {
                "type": "reschedule",
                "booking_id": "booking-001",
                "requested_slot": "2026-09-18T10:00:00-04:00",
            },
            "challenges": [{"interrupt_after_turn": 1}],
            "expectations": {
                "required_tool_calls": ["lookup_booking", "update_booking"],
                "forbidden_tool_calls": ["cancel_booking"],
                "must_confirm_before_mutation": True,
                "final_state": {
                    "booking_id": "booking-001",
                    "booking_updated": True,
                },
            },
            "tags": ["appointment"],
        }
    )


def domain_graph(workspace_id: str = "ws-a") -> dict[str, Any]:
    shared = {"schema_version": 1, "created_at": NOW}
    agent = AgentUnderTest(
        **shared,
        id="ocagent_appointment",
        name="Appointment agent",
        description="Appointment reliability target.",
        workspace_id=workspace_id,
    )
    version = AgentVersion(
        **shared,
        id="ocagentv_candidate",
        agent_id=agent.id,
        version="candidate",
        adapter_kind=AdapterKind.MOCK,
        config_sha256=SHA,
    )
    suite = ScenarioSuite(
        **shared,
        id="ocsuite_appointments",
        name="Appointments",
        description="Appointment regression suite.",
    )
    persona = Persona(
        **shared,
        id="ocpersona_calm",
        name="Calm caller",
        language="en-US",
        tone="calm",
        verbosity=Verbosity.SHORT,
    )
    scenario = Scenario(
        **shared,
        id="appointment.basic",
        suite_id=suite.id,
        persona_id=persona.id,
        name="Basic appointment",
        initial_message="Move my appointment.",
        goal_type="reschedule",
        source_sha256="b" * 64,
    )
    campaign = Campaign(
        **shared,
        id="occampaign_candidate",
        agent_version_id=version.id,
        scenario_suite_id=suite.id,
        status=CampaignStatus.COMPLETED,
        mis_task_id="tsk_a" if workspace_id == "ws-a" else "tsk_b",
        mis_plan_id="plan_a" if workspace_id == "ws-a" else "plan_b",
    )
    run = ConversationRun(
        **shared,
        id="ocrun_basic",
        campaign_id=campaign.id,
        scenario_id=scenario.id,
        agent_version_id=version.id,
        status=RunFinalState.PASS,
        mis_run_id="run_a" if workspace_id == "ws-a" else "run_b",
    )
    user_turn = ConversationTurn(
        **shared,
        id="octurn_user",
        run_id=run.id,
        turn_index=0,
        role=TurnRole.USER,
        content="Move my appointment.",
    )
    agent_turn = ConversationTurn(
        **shared,
        id="octurn_agent",
        run_id=run.id,
        turn_index=1,
        role=TurnRole.ASSISTANT,
        content="Appointment updated.",
    )
    tool_call = ObservedToolCall(
        **shared,
        id="octool_update",
        run_id=run.id,
        turn_id=agent_turn.id,
        name="update_booking",
        arguments={"booking_id": "booking-001"},
        result={"booking_updated": True},
        error=None,
        is_mutation=True,
        duration_ms=10,
        mis_tool_call_id="tc_a" if workspace_id == "ws-a" else "tc_b",
    )
    evaluation = EvaluationResult(
        **shared,
        id="evr_confirmation_before_mutation",
        run_id=run.id,
        evaluator_id="confirmation_before_mutation.v1",
        status=EvaluationStatus.FAIL,
        score=0.0,
        threshold=1.0,
        reason_codes=["confirmation_missing"],
        evidence_refs=[f"tool_call:{tool_call.id}"],
        metadata={"required": True},
        mis_evaluation_id="eval_a" if workspace_id == "ws-a" else "eval_b",
    )
    failure = FailureCase(
        **shared,
        id="ocfailure_confirmation",
        run_id=run.id,
        scenario_id=scenario.id,
        evaluation_result_id=evaluation.id,
        reason_code="confirmation_missing",
        expected={"confirmed": True},
        observed={"confirmed": False},
        evidence_refs=[f"turn:{user_turn.id}"],
    )
    cluster = FailureCluster(
        **shared,
        id="occluster_confirmation",
        campaign_id=campaign.id,
        signature="confirmation_missing:update_booking",
        failure_case_ids=[failure.id],
    )
    regression = RegressionCase(
        **shared,
        id="ocregression_confirmation",
        failure_case_id=failure.id,
        scenario_id=scenario.id,
        source_run_id=run.id,
        name="Confirm before update",
        original_input={"message": "Move my appointment."},
        expected={"confirmed": True},
        observed={"confirmed": False},
        reason_code="confirmation_missing",
        evaluator_id="confirmation_before_mutation.v1",
        evidence_refs=[f"evaluation:{evaluation.id}"],
        mis_memory_id="mem_a" if workspace_id == "ws-a" else "mem_b",
    )
    gate = ReleaseGateDecision(
        **shared,
        id="ocgate_candidate",
        campaign_id=campaign.id,
        baseline_campaign_id=None,
        decision=GateDecision.PASS,
        policy_version="release_gate.v1",
        blockers=[],
        warnings=[],
        metrics={"task_success_rate": 1.0},
        evidence_refs=["artifact:campaign_summary.json"],
        mis_approval_id="ap_a" if workspace_id == "ws-a" else "ap_b",
    )
    manifest = EvidenceManifest(
        **shared,
        id="ocmanifest_basic",
        campaign_id=campaign.id,
        run_id=run.id,
        mis_artifact_id="art_a" if workspace_id == "ws-a" else "art_b",
        mis_plan_evidence_manifest_id="pem_a" if workspace_id == "ws-a" else "pem_b",
        git_commit_sha="c" * 40,
        environment=EvidenceEnvironment(
            os="Windows-11",
            python_version="3.11.9",
            node_version="v20.19.0",
        ),
        scenario_sha256="d" * 64,
        agent_config_sha256="e" * 64,
        evaluator_versions=["task_success.v1"],
        artifacts={
            "scenario.yaml": "d" * 64,
            "agent_version.json": "e" * 64,
        },
        started_at=NOW - timedelta(seconds=1),
        finished_at=NOW,
        final_state=RunFinalState.PASS,
    )
    return locals()


def persist_graph(repo: Any, graph: dict[str, Any]) -> None:
    repo.upsert_agent(graph["agent"])
    repo.upsert_agent_version(graph["version"])
    repo.upsert_scenario_suite(graph["suite"])
    repo.upsert_persona(graph["persona"])
    repo.upsert_scenario(graph["scenario"], contract=scenario_contract())
    repo.upsert_campaign(graph["campaign"])
    repo.upsert_run_projection(
        graph["run"],
        turns=[graph["user_turn"], graph["agent_turn"]],
        tool_calls=[graph["tool_call"]],
        evaluations=[graph["evaluation"]],
    )
    repo.upsert_failure(graph["failure"])
    repo.upsert_failure_cluster(graph["cluster"])
    repo.upsert_regression(graph["regression"])
    repo.upsert_release_gate(graph["gate"])
    repo.upsert_evidence_manifest(graph["manifest"])


def test_schema_is_idempotent_normalized_and_has_no_shadow_ledgers(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "normalized.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        repo.initialize_schema()

        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "reliability_agents",
            "reliability_agent_versions",
            "reliability_scenario_suites",
            "reliability_scenarios",
            "reliability_campaigns",
            "reliability_conversation_runs",
            "reliability_conversation_turns",
            "reliability_observed_tool_calls",
            "reliability_evaluation_results",
            "reliability_failures",
            "reliability_regressions",
            "reliability_release_gates",
            "reliability_evidence_manifests",
        }.issubset(tables)
        assert {
            "reliability_tasks",
            "reliability_approvals",
            "reliability_audit_logs",
            "reliability_memories",
        }.isdisjoint(tables)

        campaign_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(reliability_campaigns)")
        }
        assert {
            "campaign_id",
            "workspace_id",
            "schema_version",
            "agent_version_id",
            "scenario_suite_id",
            "mis_task_id",
            "mis_plan_id",
            "created_at",
        }.issubset(campaign_columns)
        for table in tables:
            if not table.startswith("reliability_"):
                continue
            columns = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            assert "payload_json" not in columns
            assert "object_json" not in columns
    finally:
        conn.close()


def test_schema_initialization_requires_the_existing_mis_authority_ledger(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = sqlite3.connect(tmp_path / "missing-authority.db")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        with pytest.raises(repository.RepositoryError, match="MIS authority schema"):
            repo.initialize_schema()
        assert (
            conn.execute(
                """SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name LIKE 'reliability_%'"""
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_secret_like_structural_ids_are_never_rewritten_by_redaction(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "structural-ids.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        agent = domain_graph()["agent"].model_copy(
            update={"id": "agtok_valid_entity_id"}
        )
        assert repo.upsert_agent(agent) == "created"
        assert repo.get_agent(agent.id)["agent_id"] == agent.id
        assert repo.list_agents()[0]["agent_id"] == agent.id
        assert (
            conn.execute("SELECT agent_id FROM reliability_agents").fetchone()[0]
            == agent.id
        )
    finally:
        conn.close()


def test_full_governed_graph_uses_the_real_mis_schema_without_fk_drift(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_actual_mis_database(tmp_path / "actual-mis.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        persist_graph(repo, domain_graph())

        reliability_tables = {
            row["name"]
            for row in conn.execute(
                """SELECT name FROM sqlite_master
                WHERE type='table' AND name LIKE 'reliability_%'"""
            )
        }
        assert len(reliability_tables) == 16
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_full_graph_round_trips_as_workspace_bounded_api_read_models(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "readback.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        assert isinstance(repo, repository.Repository)
        repo.initialize_schema()
        graph = domain_graph()

        persist_graph(repo, graph)
        persist_graph(repo, graph)

        assert [item["agent_id"] for item in repo.list_agents()] == [graph["agent"].id]
        assert (
            repo.get_agent(graph["agent"].id)["versions"][0]["agent_version_id"]
            == graph["version"].id
        )
        assert repo.list_scenario_suites()[0]["scenario_count"] == 1
        suite = repo.get_scenario_suite(graph["suite"].id)
        assert suite["scenarios"][0]["contract"]["goal"]["type"] == "reschedule"
        assert repo.get_scenario(graph["scenario"].id)["contract"]["tags"] == [
            "appointment"
        ]

        campaign = repo.get_campaign(graph["campaign"].id)
        assert campaign["mis_task_id"] == "tsk_a"
        assert campaign["mis_plan_id"] == "plan_a"
        assert campaign["run_count"] == 1
        assert len(repo.list_campaigns()) == 1

        detail = repo.get_run(graph["run"].id)
        assert detail["run"]["mis_run_id"] == "run_a"
        assert [turn["turn_index"] for turn in detail["turns"]] == [0, 1]
        assert detail["tool_calls"][0]["arguments"] == {"booking_id": "booking-001"}
        assert detail["tool_calls"][0]["mis_tool_call_id"] == "tc_a"
        assert detail["evaluations"][0]["metadata"] == {"required": True}
        assert detail["evaluations"][0]["mis_evaluation_id"] == "eval_a"
        assert detail["failures"][0]["failure_id"] == graph["failure"].id
        assert detail["regressions"][0]["mis_memory_id"] == "mem_a"
        assert detail["manifests"][0]["mis_artifact_id"] == "art_a"

        assert (
            repo.get_failure(graph["failure"].id)["reason_code"]
            == "confirmation_missing"
        )
        assert repo.list_failures()[0]["evidence_refs"] == [
            f"turn:{graph['user_turn'].id}"
        ]
        assert repo.list_failure_clusters()[0]["member_count"] == 1
        cluster = repo.get_failure_cluster(graph["cluster"].id)
        assert cluster["failure_case_ids"] == [graph["failure"].id]
        assert cluster["failures"][0]["reason_code"] == "confirmation_missing"
        assert (
            repo.get_regression(graph["regression"].id)["source_run_id"]
            == graph["run"].id
        )
        assert repo.list_regressions()[0]["mis_memory_id"] == "mem_a"
        assert repo.get_release_gate(graph["gate"].id)["mis_approval_id"] == "ap_a"
        assert repo.list_release_gates()[0]["metrics"] == {"task_success_rate": 1.0}
        assert (
            repo.get_evidence_manifest(graph["manifest"].id)[
                "mis_plan_evidence_manifest_id"
            ]
            == "pem_a"
        )
        assert (
            repo.list_evidence_manifests()[0]["artifacts"]["scenario.yaml"] == "d" * 64
        )

        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "reliability_agents",
                "reliability_campaigns",
                "reliability_conversation_runs",
                "reliability_observed_tool_calls",
                "reliability_evaluation_results",
                "reliability_evidence_manifests",
            )
        }
        assert set(counts.values()) == {1}

        other_workspace = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-b")
        assert other_workspace.list_agents() == []
        assert other_workspace.get_campaign(graph["campaign"].id) is None
        assert other_workspace.get_run(graph["run"].id) is None
    finally:
        conn.close()


def test_list_read_models_support_stable_offset_and_vertical_filters(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "pagination.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        assert repo.list_agents(limit=10, offset=1) == []
        assert repo.list_scenario_suites(limit=10, offset=1) == []
        assert repo.list_scenarios(limit=10, offset=1) == []
        assert repo.list_campaigns(limit=10, offset=1) == []
        assert repo.list_runs(limit=10, offset=1) == []
        assert repo.list_failures(limit=10, offset=1) == []
        assert repo.list_failure_clusters(limit=10, offset=1) == []
        assert repo.list_regressions(limit=10, offset=1) == []
        assert repo.list_release_gates(limit=10, offset=1) == []
        assert repo.list_evidence_manifests(limit=10, offset=1) == []

        assert repo.list_regressions(scenario_id="unknown") == []
        assert repo.list_regressions(source_run_id="unknown") == []
        assert repo.list_release_gates(campaign_id="unknown") == []
    finally:
        conn.close()


def test_missing_or_cross_workspace_mis_mappings_fail_closed_and_roll_back(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "mapping.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        repo.upsert_scenario(graph["scenario"], contract=scenario_contract())

        partial_campaign = graph["campaign"].model_copy(
            update={
                "id": "occampaign_partial_authority",
                "mis_task_id": None,
                "mis_plan_id": "plan_a",
            }
        )
        with pytest.raises(
            repository.AuthorityMappingError, match="task and plan together"
        ):
            repo.upsert_campaign(partial_campaign)

        projection_only_campaign = graph["campaign"].model_copy(
            update={
                "id": "occampaign_projection_only",
                "mis_task_id": None,
                "mis_plan_id": None,
            }
        )
        assert repo.upsert_campaign(projection_only_campaign) == "created"
        partially_mapped_run = graph["run"].model_copy(
            update={
                "id": "ocrun_projection_with_mis_run",
                "campaign_id": projection_only_campaign.id,
            }
        )
        with pytest.raises(
            repository.AuthorityMappingError, match="campaign authority"
        ):
            repo.upsert_run_projection(
                partially_mapped_run,
                turns=[],
                tool_calls=[],
                evaluations=[],
            )

        cross_workspace_campaign = graph["campaign"].model_copy(
            update={"id": "occampaign_cross_workspace", "mis_task_id": "tsk_b"}
        )
        with pytest.raises(repository.AuthorityMappingError, match="workspace"):
            repo.upsert_campaign(cross_workspace_campaign)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_campaigns WHERE campaign_id=?",
                (cross_workspace_campaign.id,),
            ).fetchone()[0]
            == 0
        )

        repo.upsert_campaign(graph["campaign"])
        rollback_run = graph["run"].model_copy(
            update={"id": "ocrun_rollback", "mis_run_id": "run_a_rollback"}
        )
        rollback_turn = graph["agent_turn"].model_copy(
            update={"id": "octurn_rollback", "run_id": rollback_run.id}
        )
        missing_mapping_call = graph["tool_call"].model_copy(
            update={
                "id": "octool_missing_mapping",
                "run_id": rollback_run.id,
                "turn_id": rollback_turn.id,
                "mis_tool_call_id": "tc_missing",
            }
        )
        with pytest.raises(repository.AuthorityMappingError, match="tc_missing"):
            repo.upsert_run_projection(
                rollback_run,
                turns=[rollback_turn],
                tool_calls=[missing_mapping_call],
                evaluations=[],
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_conversation_runs WHERE run_id=?",
                (rollback_run.id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM reliability_conversation_turns WHERE run_id=?",
                (rollback_run.id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_repository_never_commits_or_owns_the_caller_connection(tmp_path: Path) -> None:
    _, sqlite_repository = storage_modules()
    database_path = tmp_path / "caller-owned.db"
    conn = open_database(database_path)
    repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
    repo.initialize_schema()
    conn.commit()

    conn.execute("BEGIN")
    repo.upsert_agent(domain_graph()["agent"])
    conn.rollback()
    assert repo.list_agents() == []

    conn.close()
    moved = database_path.with_name("caller-owned-moved.db")
    database_path.replace(moved)
    moved.replace(database_path)
    assert database_path.is_file()


def test_run_projection_cannot_cross_link_the_campaign_agent_version(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "graph-parent.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        repo.upsert_scenario(graph["scenario"], contract=scenario_contract())
        repo.upsert_campaign(graph["campaign"])

        unrelated_version = graph["version"].model_copy(
            update={"id": "ocagentv_unrelated", "version": "unrelated"}
        )
        repo.upsert_agent_version(unrelated_version)
        cross_linked_run = graph["run"].model_copy(
            update={
                "id": "ocrun_cross_linked_version",
                "agent_version_id": unrelated_version.id,
            }
        )

        with pytest.raises(
            repository.RepositoryConflictError, match="campaign agent version"
        ):
            repo.upsert_run_projection(
                cross_linked_run,
                turns=[],
                tool_calls=[],
                evaluations=[],
            )
        assert repo.get_run(cross_linked_run.id) is None
    finally:
        conn.close()


def test_failure_and_regression_parents_must_form_one_run_graph(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "failure-graph.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        other_run = graph["run"].model_copy(
            update={"id": "ocrun_other", "mis_run_id": "run_a_rollback"}
        )
        repo.upsert_run_projection(
            other_run,
            turns=[],
            tool_calls=[],
            evaluations=[],
        )
        cross_failure = graph["failure"].model_copy(
            update={"id": "ocfailure_cross", "run_id": other_run.id}
        )
        with pytest.raises(
            repository.RepositoryConflictError, match="failure evaluation"
        ):
            repo.upsert_failure(cross_failure)

        cross_regression = graph["regression"].model_copy(
            update={"id": "ocregression_cross", "source_run_id": other_run.id}
        )
        with pytest.raises(
            repository.RepositoryConflictError, match="failure source run"
        ):
            repo.upsert_regression(cross_regression)

        other_campaign = graph["campaign"].model_copy(
            update={
                "id": "occampaign_other",
                "mis_task_id": None,
                "mis_plan_id": None,
            }
        )
        repo.upsert_campaign(other_campaign)
        cross_cluster = graph["cluster"].model_copy(
            update={"id": "occluster_cross", "campaign_id": other_campaign.id}
        )
        with pytest.raises(repository.RepositoryConflictError, match="cluster failure"):
            repo.upsert_failure_cluster(cross_cluster)
    finally:
        conn.close()


def test_child_mis_mappings_require_a_mapped_parent_run(tmp_path: Path) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "partial-mapping.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        repo.upsert_scenario(graph["scenario"], contract=scenario_contract())
        projection_campaign = graph["campaign"].model_copy(
            update={
                "id": "occampaign_child_projection",
                "mis_task_id": None,
                "mis_plan_id": None,
            }
        )
        repo.upsert_campaign(projection_campaign)

        tool_run = graph["run"].model_copy(
            update={
                "id": "ocrun_unmapped_tool",
                "campaign_id": projection_campaign.id,
                "mis_run_id": None,
            }
        )
        tool_turn = graph["agent_turn"].model_copy(
            update={"id": "octurn_unmapped_tool", "run_id": tool_run.id}
        )
        mapped_call = graph["tool_call"].model_copy(
            update={
                "id": "octool_child_without_parent",
                "run_id": tool_run.id,
                "turn_id": tool_turn.id,
            }
        )
        with pytest.raises(repository.AuthorityMappingError, match="mapped MIS run"):
            repo.upsert_run_projection(
                tool_run,
                turns=[tool_turn],
                tool_calls=[mapped_call],
                evaluations=[],
            )

        evaluation_run = graph["run"].model_copy(
            update={
                "id": "ocrun_unmapped_evaluation",
                "campaign_id": projection_campaign.id,
                "mis_run_id": None,
            }
        )
        mapped_evaluation = graph["evaluation"].model_copy(
            update={"id": "evr_child_without_parent", "run_id": evaluation_run.id}
        )
        with pytest.raises(repository.AuthorityMappingError, match="mapped MIS run"):
            repo.upsert_run_projection(
                evaluation_run,
                turns=[],
                tool_calls=[],
                evaluations=[mapped_evaluation],
            )

        assert repo.get_run(tool_run.id) is None
        assert repo.get_run(evaluation_run.id) is None
    finally:
        conn.close()


def test_stable_observation_and_evaluation_ids_cannot_rewrite_facts(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "immutable-facts.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        changed_call = graph["tool_call"].model_copy(
            update={"result": {"booking_updated": False}}
        )
        with pytest.raises(repository.RepositoryConflictError, match="result_json"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[graph["user_turn"], graph["agent_turn"]],
                tool_calls=[changed_call],
                evaluations=[graph["evaluation"]],
            )

        changed_evaluation = graph["evaluation"].model_copy(
            update={
                "status": EvaluationStatus.PASS,
                "score": 1.0,
                "reason_codes": [],
            }
        )
        with pytest.raises(repository.RepositoryError):
            repo.upsert_run_projection(
                graph["run"],
                turns=[graph["user_turn"], graph["agent_turn"]],
                tool_calls=[graph["tool_call"]],
                evaluations=[changed_evaluation],
            )
    finally:
        conn.close()


def test_authoritative_mis_ids_are_claimed_by_only_one_vertical_object(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "mapping-uniqueness.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        duplicate_run = graph["run"].model_copy(
            update={"id": "ocrun_duplicate_mapping"}
        )
        with pytest.raises(repository.RepositoryConflictError, match="mapping"):
            repo.upsert_run_projection(
                duplicate_run,
                turns=[],
                tool_calls=[],
                evaluations=[],
            )

        duplicate_call = graph["tool_call"].model_copy(
            update={"id": "octool_duplicate_mapping"}
        )
        with pytest.raises(repository.RepositoryConflictError, match="mapping"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[graph["user_turn"], graph["agent_turn"]],
                tool_calls=[graph["tool_call"], duplicate_call],
                evaluations=[graph["evaluation"]],
            )

        duplicate_evaluation = graph["evaluation"].model_copy(
            update={"id": "evr_duplicate_mapping"}
        )
        with pytest.raises(repository.RepositoryConflictError, match="mapping"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[graph["user_turn"], graph["agent_turn"]],
                tool_calls=[graph["tool_call"]],
                evaluations=[graph["evaluation"], duplicate_evaluation],
            )
    finally:
        conn.close()


def test_governed_rows_require_complete_child_authority_mappings(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "complete-authority.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        unmapped_call = graph["tool_call"].model_copy(update={"mis_tool_call_id": None})
        with pytest.raises(
            repository.AuthorityMappingError, match="tool call mapping is required"
        ):
            repo.upsert_run_projection(
                graph["run"],
                turns=[graph["user_turn"], graph["agent_turn"]],
                tool_calls=[unmapped_call],
                evaluations=[graph["evaluation"]],
            )

        unmapped_regression = graph["regression"].model_copy(
            update={"mis_memory_id": None}
        )
        with pytest.raises(
            repository.AuthorityMappingError, match="memory mapping is required"
        ):
            repo.upsert_regression(unmapped_regression)

        unmapped_gate = graph["gate"].model_copy(update={"mis_approval_id": None})
        with pytest.raises(
            repository.AuthorityMappingError, match="approval mapping is required"
        ):
            repo.upsert_release_gate(unmapped_gate)

        unmapped_manifest = graph["manifest"].model_copy(
            update={
                "mis_artifact_id": None,
                "mis_plan_evidence_manifest_id": None,
            }
        )
        with pytest.raises(
            repository.AuthorityMappingError, match="artifact mapping is required"
        ):
            repo.upsert_evidence_manifest(unmapped_manifest)
    finally:
        conn.close()


def test_failure_cluster_members_are_immutable_for_a_stable_cluster_id(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "cluster-members.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        second_failure = graph["failure"].model_copy(
            update={"id": "ocfailure_confirmation_second"}
        )
        repo.upsert_failure(second_failure)
        rewritten_cluster = graph["cluster"].model_copy(
            update={"failure_case_ids": [graph["failure"].id, second_failure.id]}
        )
        with pytest.raises(repository.RepositoryConflictError, match="cluster members"):
            repo.upsert_failure_cluster(rewritten_cluster)
    finally:
        conn.close()


def test_authoritative_run_tool_and_evaluation_semantics_must_match(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "semantic-authority.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        repo.upsert_scenario(graph["scenario"], contract=scenario_contract())
        repo.upsert_campaign(graph["campaign"])

        conn.execute("UPDATE runs SET agent_plan_id='plan_b' WHERE run_id='run_a'")
        with pytest.raises(repository.AuthorityMappingError, match="campaign plan"):
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[]
            )
        conn.execute("UPDATE runs SET agent_plan_id='plan_a' WHERE run_id='run_a'")

        conn.execute(
            "UPDATE tool_calls SET tool_name='lookup_booking' WHERE tool_call_id='tc_a'"
        )
        with pytest.raises(repository.AuthorityMappingError, match="tool call name"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[graph["agent_turn"]],
                tool_calls=[graph["tool_call"]],
                evaluations=[],
            )
        conn.execute(
            "UPDATE tool_calls SET tool_name='update_booking' WHERE tool_call_id='tc_a'"
        )

        conn.execute("UPDATE evaluations SET score=1.0 WHERE evaluation_id='eval_a'")
        with pytest.raises(repository.AuthorityMappingError, match="evaluation score"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[],
                tool_calls=[],
                evaluations=[graph["evaluation"]],
            )
        conn.execute(
            "UPDATE evaluations SET score=0.0,pass_fail='pass' WHERE evaluation_id='eval_a'"
        )
        with pytest.raises(repository.AuthorityMappingError, match="evaluation status"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[],
                tool_calls=[],
                evaluations=[graph["evaluation"]],
            )
        conn.execute(
            """UPDATE evaluations
            SET pass_fail='fail',rubric_json='{"evaluator_id":"other.v1","vertical_status":"fail"}'
            WHERE evaluation_id='eval_a'"""
        )
        with pytest.raises(repository.AuthorityMappingError, match="evaluator id"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[],
                tool_calls=[],
                evaluations=[graph["evaluation"]],
            )
        conn.execute(
            """UPDATE evaluations
            SET rubric_json='{"evaluator_id":"confirmation_before_mutation.v1","vertical_status":"fail"}',
                task_id='tsk_b'
            WHERE evaluation_id='eval_a'"""
        )
        with pytest.raises(repository.AuthorityMappingError, match="campaign task"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[],
                tool_calls=[],
                evaluations=[graph["evaluation"]],
            )
        conn.execute(
            "UPDATE evaluations SET task_id='tsk_a',agent_id='agt_b' WHERE evaluation_id='eval_a'"
        )
        with pytest.raises(repository.AuthorityMappingError, match="run agent"):
            repo.upsert_run_projection(
                graph["run"],
                turns=[],
                tool_calls=[],
                evaluations=[graph["evaluation"]],
            )
    finally:
        conn.close()


def test_evaluation_status_mapping_is_total_and_skipped_is_never_fake_scored(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "evaluation-status-authority.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        repo.upsert_scenario(graph["scenario"], contract=scenario_contract())
        repo.upsert_campaign(graph["campaign"])

        skipped = graph["evaluation"].model_copy(
            update={
                "id": "evr_optional_judge",
                "evaluator_id": "llm_judge.v1",
                "status": EvaluationStatus.SKIPPED,
                "score": None,
                "threshold": None,
                "reason_codes": ["judge_unavailable"],
            }
        )
        with pytest.raises(repository.AuthorityMappingError, match="SKIPPED"):
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[skipped]
            )
        skipped = skipped.model_copy(update={"mis_evaluation_id": None})
        assert (
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[skipped]
            )
            == "created"
        )

        error = graph["evaluation"].model_copy(
            update={
                "id": "evr_rule_error",
                "evaluator_id": "timeout.v1",
                "status": EvaluationStatus.ERROR,
                "score": None,
                "threshold": None,
                "reason_codes": ["evaluator_error"],
            }
        )
        conn.execute(
            """UPDATE evaluations
            SET score=1.0,pass_fail='fail',
                rubric_json='{"evaluator_id":"timeout.v1","vertical_status":"error"}'
            WHERE evaluation_id='eval_a'"""
        )
        with pytest.raises(repository.AuthorityMappingError, match="error score"):
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[error]
            )
        conn.execute("UPDATE evaluations SET score=0.0 WHERE evaluation_id='eval_a'")
        assert (
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[error]
            )
            == "unchanged"
        )

        conn.execute(
            """INSERT INTO evaluations(
                evaluation_id,run_id,task_id,agent_id,score,pass_fail,rubric_json
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                "eval_warn",
                "run_a",
                "tsk_a",
                "agt_a",
                0.5,
                "fail",
                '{"evaluator_id":"turn_count_limit.v1","vertical_status":"warn"}',
            ),
        )
        warn = graph["evaluation"].model_copy(
            update={
                "id": "evr_rule_warn",
                "evaluator_id": "turn_count_limit.v1",
                "status": EvaluationStatus.WARN,
                "score": 0.5,
                "threshold": 1.0,
                "reason_codes": ["near_limit"],
                "mis_evaluation_id": "eval_warn",
            }
        )
        assert (
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[warn]
            )
            == "unchanged"
        )

        conn.execute(
            """INSERT INTO evaluations(
                evaluation_id,run_id,task_id,agent_id,score,pass_fail,rubric_json
            ) VALUES(?,?,?,?,?,?,?)""",
            ("eval_missing", "run_a", "tsk_a", "agt_a", 0.0, "fail", "{}"),
        )
        missing_rubric = graph["evaluation"].model_copy(
            update={"id": "evr_missing_rubric", "mis_evaluation_id": "eval_missing"}
        )
        with pytest.raises(repository.AuthorityMappingError, match="rubric evaluator"):
            repo.upsert_run_projection(
                graph["run"], turns=[], tool_calls=[], evaluations=[missing_rubric]
            )
    finally:
        conn.close()


def test_governed_gate_regression_and_evidence_require_semantic_authority(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "governed-semantics.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)

        conn.execute("UPDATE memories SET memory_type='policy' WHERE memory_id='mem_a'")
        with pytest.raises(repository.AuthorityMappingError, match="failure_case"):
            repo.upsert_regression(graph["regression"])
        conn.execute(
            "UPDATE memories SET memory_type='failure_case' WHERE memory_id='mem_a'"
        )

        conn.execute("UPDATE memories SET task_id=NULL WHERE memory_id='mem_a'")
        with pytest.raises(repository.AuthorityMappingError, match="campaign task"):
            repo.upsert_regression(graph["regression"])
        conn.execute(
            "UPDATE memories SET task_id='tsk_a',source_ref='run_b' WHERE memory_id='mem_a'"
        )
        with pytest.raises(repository.AuthorityMappingError, match="source run"):
            repo.upsert_regression(graph["regression"])
        conn.execute(
            "UPDATE memories SET source_ref='run_a',review_status='approved' WHERE memory_id='mem_a'"
        )
        with pytest.raises(repository.AuthorityMappingError, match="candidate"):
            repo.upsert_regression(graph["regression"])
        conn.execute(
            "UPDATE memories SET review_status='candidate' WHERE memory_id='mem_a'"
        )

        conn.execute(
            "UPDATE approvals SET subject_type='task' WHERE approval_id='ap_a'"
        )
        with pytest.raises(
            repository.AuthorityMappingError, match="release gate subject"
        ):
            repo.upsert_release_gate(graph["gate"])
        conn.execute(
            """UPDATE approvals
            SET subject_type='reliability_release_gate',decision='rejected'
            WHERE approval_id='ap_a'"""
        )
        with pytest.raises(repository.AuthorityMappingError, match="gate decision"):
            repo.upsert_release_gate(graph["gate"])
        conn.execute(
            "UPDATE approvals SET decision='approved' WHERE approval_id='ap_a'"
        )

        conn.execute(
            """INSERT INTO artifacts(
                artifact_id,task_id,run_id,artifact_type,content_hash
            ) VALUES(?,?,?,?,?)""",
            ("art_without_plan_evidence", "tsk_a", "run_a", "evidence", "a" * 64),
        )
        missing_plan_evidence = graph["manifest"].model_copy(
            update={
                "id": "ocmanifest_missing_plan_evidence",
                "mis_artifact_id": "art_without_plan_evidence",
                "mis_plan_evidence_manifest_id": None,
            }
        )
        assert repo.upsert_evidence_manifest(missing_plan_evidence) == "created"
        assert (
            repo.get_evidence_manifest(missing_plan_evidence.id)[
                "mis_plan_evidence_manifest_id"
            ]
            is None
        )

        conn.execute(
            """INSERT INTO artifacts(
                artifact_id,task_id,run_id,artifact_type,content_hash
            ) VALUES(?,?,?,?,?)""",
            ("art_unrelated", "tsk_a", "run_a", "evidence", "f" * 64),
        )
        unrelated_artifact = graph["manifest"].model_copy(
            update={
                "id": "ocmanifest_unrelated_artifact",
                "mis_artifact_id": "art_unrelated",
            }
        )
        with pytest.raises(repository.AuthorityMappingError, match="declared artifact"):
            repo.upsert_evidence_manifest(unrelated_artifact)

        conn.execute(
            "UPDATE plan_evidence_manifests SET status='blocked' WHERE manifest_id='pem_a'"
        )
        with pytest.raises(repository.AuthorityMappingError, match="not verified"):
            repo.upsert_evidence_manifest(graph["manifest"])
    finally:
        conn.close()


def test_run_detail_recursively_redacts_secrets_and_hidden_prompts(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "safe-read-model.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        private_contract = scenario_contract()
        private_contract = private_contract.model_copy(
            update={
                "expectations": private_contract.expectations.model_copy(
                    update={
                        "final_state": {
                            "booking_updated": True,
                            "system_prompt": "scenario-secret",
                        }
                    }
                )
            }
        )
        repo.upsert_scenario(graph["scenario"], contract=private_contract)
        campaign = graph["campaign"].model_copy(
            update={"mis_task_id": None, "mis_plan_id": None}
        )
        repo.upsert_campaign(campaign)
        run = graph["run"].model_copy(update={"mis_run_id": None})
        turn = graph["agent_turn"].model_copy(
            update={
                "content": (
                    "Authorization: Bearer TOPSECRET123; cookie=session-cookie-secret"
                )
            }
        )
        call = graph["tool_call"].model_copy(
            update={
                "arguments": {
                    "booking_id": "booking-001",
                    "apiKey": "tool-secret",
                    "cookie": "cookie-secret",
                },
                "result": {
                    "booking_updated": True,
                    "access_token": "result-secret",
                    "raw_model_response": "model-response-secret",
                },
                "mis_tool_call_id": None,
            }
        )
        evaluation = graph["evaluation"].model_copy(
            update={
                "metadata": {
                    "safe_reason": "confirmation required",
                    "password": "metadata-secret",
                    "rawPrompt": "hidden-secret",
                    "system_prompt": "system-prompt-secret",
                    "messages": [
                        {"role": "system", "content": "system-message-secret"},
                        {
                            "role": "developer",
                            "content": "developer-message-secret",
                        },
                        {"role": "user", "content": "safe user content"},
                    ],
                    "cookies": {"session": "cookies-secret"},
                    "cookieJar": {"session": "cookie-jar-secret"},
                    "credentials": {"username": "credentials-secret"},
                    "api_keys": ["api-keys-secret"],
                    "tokens": ["tokens-secret"],
                    "secrets": ["secrets-container-secret"],
                    "session_id": "session-id-secret",
                    "token_id": "token-id-secret",
                    "credentials_id": "credentials-id-secret",
                    "mis_links": {"note": "Authorization: Bearer FAKEMIS_SECRET"},
                },
                "mis_evaluation_id": None,
                "evidence_refs": ["Authorization: Bearer EVREF_SECRET"],
            }
        )
        repo.upsert_run_projection(
            run,
            turns=[turn],
            tool_calls=[call],
            evaluations=[evaluation],
        )
        failure = graph["failure"].model_copy(
            update={
                "expected": {"system_prompt": "failure-secret"},
                "observed": {"cookie": "failure-cookie-secret"},
            }
        )
        repo.upsert_failure(failure)
        regression = graph["regression"].model_copy(
            update={
                "original_input": {"raw_model_response": "regression-secret"},
                "mis_memory_id": None,
            }
        )
        repo.upsert_regression(regression)
        gate = graph["gate"].model_copy(
            update={
                "metrics": {"system_prompt": "gate-secret"},
                "mis_approval_id": None,
            }
        )
        repo.upsert_release_gate(gate)
        manifest = graph["manifest"].model_copy(
            update={
                "mis_artifact_id": None,
                "mis_plan_evidence_manifest_id": None,
                "environment": graph["manifest"].environment.model_copy(
                    update={"os": "cookie=manifest-secret"}
                ),
            }
        )
        repo.upsert_evidence_manifest(manifest)

        detail = repo.get_run(run.id)
        public_models = {
            "detail": detail,
            "scenario": repo.get_scenario(graph["scenario"].id),
            "failure": repo.get_failure(failure.id),
            "regression": repo.get_regression(regression.id),
            "gate": repo.get_release_gate(gate.id),
            "manifest": repo.get_evidence_manifest(manifest.id),
        }
        encoded = __import__("json").dumps(public_models, sort_keys=True)
        for secret in (
            "TOPSECRET123",
            "session-cookie-secret",
            "tool-secret",
            "cookie-secret",
            "result-secret",
            "model-response-secret",
            "metadata-secret",
            "hidden-secret",
            "system-prompt-secret",
            "system-message-secret",
            "developer-message-secret",
            "cookies-secret",
            "cookie-jar-secret",
            "credentials-secret",
            "api-keys-secret",
            "tokens-secret",
            "secrets-container-secret",
            "session-id-secret",
            "token-id-secret",
            "credentials-id-secret",
            "EVREF_SECRET",
            "FAKEMIS_SECRET",
            "scenario-secret",
            "failure-secret",
            "failure-cookie-secret",
            "regression-secret",
            "gate-secret",
            "manifest-secret",
        ):
            assert secret not in encoded
        assert "[REDACTED]" in encoded
        assert detail["tool_calls"][0]["arguments"]["booking_id"] == "booking-001"
        assert (
            detail["evaluations"][0]["metadata"]["safe_reason"]
            == "confirmation required"
        )
        assert (
            detail["evaluations"][0]["metadata"]["messages"][2]["content"]
            == "safe user content"
        )
        stored = "\n".join(
            str(value)
            for row in conn.execute(
                """SELECT content FROM reliability_conversation_turns
                UNION ALL SELECT arguments_json FROM reliability_observed_tool_calls
                UNION ALL SELECT result_json FROM reliability_observed_tool_calls
                UNION ALL SELECT metadata_json FROM reliability_evaluation_results
                UNION ALL SELECT evidence_refs_json FROM reliability_evaluation_results
                UNION ALL SELECT expected_json FROM reliability_failures
                UNION ALL SELECT observed_json FROM reliability_failures
                UNION ALL SELECT original_input_json FROM reliability_regressions
                UNION ALL SELECT metrics_json FROM reliability_release_gates
                UNION ALL SELECT environment_json FROM reliability_evidence_manifests
                UNION ALL SELECT expectations_json FROM reliability_scenarios"""
            )
            for value in row
        )
        for secret in (
            "TOPSECRET123",
            "tool-secret",
            "model-response-secret",
            "system-prompt-secret",
            "system-message-secret",
            "developer-message-secret",
            "cookies-secret",
            "cookie-jar-secret",
            "credentials-secret",
            "api-keys-secret",
            "tokens-secret",
            "secrets-container-secret",
            "session-id-secret",
            "token-id-secret",
            "credentials-id-secret",
            "EVREF_SECRET",
            "FAKEMIS_SECRET",
            "failure-secret",
            "regression-secret",
            "gate-secret",
            "manifest-secret",
            "scenario-secret",
        ):
            assert secret not in stored
    finally:
        conn.close()


def test_oversized_observation_json_fails_and_rolls_back_the_run(
    tmp_path: Path,
) -> None:
    repository, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "bounded-json.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        repo.upsert_agent(graph["agent"])
        repo.upsert_agent_version(graph["version"])
        repo.upsert_scenario_suite(graph["suite"])
        repo.upsert_persona(graph["persona"])
        repo.upsert_scenario(graph["scenario"], contract=scenario_contract())
        campaign = graph["campaign"].model_copy(
            update={"mis_task_id": None, "mis_plan_id": None}
        )
        repo.upsert_campaign(campaign)
        run = graph["run"].model_copy(update={"mis_run_id": None})
        call = graph["tool_call"].model_copy(
            update={
                "arguments": {"payload": "x" * (513 * 1024)},
                "mis_tool_call_id": None,
            }
        )

        with pytest.raises(repository.RepositoryError, match="exceeds"):
            repo.upsert_run_projection(
                run,
                turns=[graph["agent_turn"]],
                tool_calls=[call],
                evaluations=[],
            )
        assert repo.get_run(run.id) is None
    finally:
        conn.close()


def test_run_detail_is_bounded_and_reports_truncated_collections(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "bounded-read.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)
        conn.executemany(
            """INSERT INTO reliability_conversation_turns(
                workspace_id,turn_id,schema_version,run_id,turn_index,role,content,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            [
                (
                    "ws-a",
                    f"octurn_extra_{index}",
                    1,
                    graph["run"].id,
                    index,
                    "assistant",
                    "bounded",
                    NOW.isoformat().replace("+00:00", "Z"),
                )
                for index in range(2, 207)
            ],
        )

        detail = repo.get_run(graph["run"].id)
        assert len(detail["turns"]) == 200
        assert detail["collection_limits"] == {
            "max_items_per_collection": 200,
            "truncated": ["turns"],
        }
    finally:
        conn.close()


def test_failure_cluster_detail_streams_a_bounded_member_projection(
    tmp_path: Path,
) -> None:
    _, sqlite_repository = storage_modules()
    conn = open_database(tmp_path / "bounded-cluster-read.db")
    try:
        repo = sqlite_repository.SQLiteRepository(conn, workspace_id="ws-a")
        repo.initialize_schema()
        graph = domain_graph()
        persist_graph(repo, graph)
        failures = [
            (f"ocfailure_extra_{index}", graph["failure"].id) for index in range(205)
        ]
        conn.executemany(
            """INSERT INTO reliability_failures(
                workspace_id,failure_id,schema_version,run_id,scenario_id,
                evaluation_result_id,reason_code,expected_json,observed_json,
                evidence_refs_json,created_at
            ) SELECT workspace_id,?,schema_version,run_id,scenario_id,
                evaluation_result_id,reason_code,expected_json,observed_json,
                evidence_refs_json,created_at
              FROM reliability_failures
             WHERE workspace_id='ws-a' AND failure_id=?""",
            failures,
        )
        conn.executemany(
            """INSERT INTO reliability_failure_cluster_members(
                workspace_id,cluster_id,failure_id,ordinal
            ) VALUES(?,?,?,?)""",
            [
                ("ws-a", graph["cluster"].id, failure_id, index + 1)
                for index, (failure_id, _) in enumerate(failures)
            ],
        )

        cluster = repo.get_failure_cluster(graph["cluster"].id)
        assert cluster["member_count"] == 206
        assert cluster["returned_member_count"] == 200
        assert cluster["members_truncated"] is True
        assert len(cluster["failure_case_ids"]) == 200
    finally:
        conn.close()
