"""Normalized SQLite projection over the authoritative AgentOps MIS ledger."""

from __future__ import annotations

import itertools
import json
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from agentops_mis_cli.redaction import redact_full_text

from open_cekura.domain.models import (
    AgentUnderTest,
    AgentVersion,
    Campaign,
    ConversationRun,
    ConversationTurn,
    EvaluationResult,
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

from .repository import (
    AuthorityMappingError,
    RepositoryConflictError,
    RepositoryError,
)


_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_MAX_JSON_BYTES = 512 * 1024
_RUN_DETAIL_COLLECTION_LIMIT = 200
_MAX_RUN_DETAIL_BYTES = 8 * 1024 * 1024
_AUTHORITY_COLUMNS: dict[str, frozenset[str]] = {
    "tasks": frozenset({"task_id", "workspace_id"}),
    "agent_plans": frozenset({"plan_id", "workspace_id", "task_id"}),
    "runs": frozenset(
        {"run_id", "workspace_id", "task_id", "agent_plan_id", "agent_id"}
    ),
    "tool_calls": frozenset({"tool_call_id", "run_id", "tool_name", "agent_id"}),
    "evaluations": frozenset(
        {
            "evaluation_id",
            "run_id",
            "task_id",
            "agent_id",
            "score",
            "pass_fail",
            "rubric_json",
        }
    ),
    "artifacts": frozenset(
        {"artifact_id", "task_id", "run_id", "artifact_type", "content_hash"}
    ),
    "approvals": frozenset(
        {"approval_id", "task_id", "run_id", "decision", "subject_type", "subject_id"}
    ),
    "memories": frozenset(
        {
            "memory_id",
            "workspace_id",
            "memory_type",
            "task_id",
            "source_ref",
            "review_status",
        }
    ),
    "plan_evidence_manifests": frozenset(
        {
            "manifest_id",
            "workspace_id",
            "plan_id",
            "task_id",
            "run_id",
            "tool_call_ids_json",
            "evaluation_ids_json",
            "artifact_ids_json",
            "status",
        }
    ),
}
_SENSITIVE_PUBLIC_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "authtoken",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "credential",
        "credentials",
        "cookie",
        "cookiejar",
        "cookies",
        "apikeys",
        "password",
        "privatekey",
        "rawprompt",
        "rawmodelresponse",
        "hiddenprompt",
        "rawresponse",
        "secret",
        "secrets",
        "session",
        "sessiontoken",
        "setcookie",
        "systemprompt",
        "token",
        "tokens",
    }
)
_STRUCTURAL_PUBLIC_KEYS = frozenset(
    {
        "agentconfigsha256",
        "agentid",
        "agentversionid",
        "artifacts",
        "baselinecampaignid",
        "campaignid",
        "clusterid",
        "createdat",
        "decision",
        "evaluationid",
        "evaluationresultid",
        "evaluatorversions",
        "failurecaseid",
        "failurecaseids",
        "failureid",
        "finishedat",
        "gateid",
        "gitcommitsha",
        "id",
        "ismutation",
        "manifestid",
        "misapprovalid",
        "misartifactid",
        "misevaluationid",
        "mismemoryid",
        "misplanevidencemanifestid",
        "misplanid",
        "misrunid",
        "mistaskid",
        "mistoolcallid",
        "personaid",
        "policyversion",
        "reasoncodes",
        "regressionid",
        "role",
        "runid",
        "schemaversion",
        "scenariosha256",
        "scenarioid",
        "signature",
        "sourcerunid",
        "startedat",
        "status",
        "suiteid",
        "threshold",
        "toolcallid",
        "turnid",
        "version",
        "workspaceid",
    }
)
_DOMAIN_ROW_ID_KEYS = frozenset(
    {
        "agentid",
        "agentversionid",
        "campaignid",
        "clusterid",
        "evaluationid",
        "failureid",
        "gateid",
        "manifestid",
        "personaid",
        "regressionid",
        "runid",
        "scenarioid",
        "suiteid",
        "toolcallid",
        "turnid",
    }
)
_ROW_CONTAINER_PUBLIC_KEYS = frozenset(
    {
        "evaluations",
        "failures",
        "manifests",
        "regressions",
        "releasegates",
        "run",
        "scenarios",
        "toolcalls",
        "turns",
        "versions",
    }
)


RELIABILITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reliability_agents (
    workspace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, agent_id)
);

CREATE TABLE IF NOT EXISTS reliability_agent_versions (
    workspace_id TEXT NOT NULL,
    agent_version_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    agent_id TEXT NOT NULL,
    version TEXT NOT NULL,
    adapter_kind TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, agent_version_id),
    FOREIGN KEY(workspace_id, agent_id)
        REFERENCES reliability_agents(workspace_id, agent_id)
);

CREATE TABLE IF NOT EXISTS reliability_scenario_suites (
    workspace_id TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, suite_id)
);

CREATE TABLE IF NOT EXISTS reliability_personas (
    workspace_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    tone TEXT NOT NULL,
    verbosity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, persona_id)
);

CREATE TABLE IF NOT EXISTS reliability_scenarios (
    workspace_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    suite_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    name TEXT NOT NULL,
    initial_message TEXT NOT NULL,
    goal_type TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    persona_json TEXT NOT NULL,
    goal_json TEXT NOT NULL,
    challenges_json TEXT NOT NULL,
    expectations_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, scenario_id),
    FOREIGN KEY(workspace_id, suite_id)
        REFERENCES reliability_scenario_suites(workspace_id, suite_id),
    FOREIGN KEY(workspace_id, persona_id)
        REFERENCES reliability_personas(workspace_id, persona_id)
);

CREATE TABLE IF NOT EXISTS reliability_campaigns (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    agent_version_id TEXT NOT NULL,
    scenario_suite_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mis_task_id TEXT,
    mis_plan_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, campaign_id),
    FOREIGN KEY(workspace_id, agent_version_id)
        REFERENCES reliability_agent_versions(workspace_id, agent_version_id),
    FOREIGN KEY(workspace_id, scenario_suite_id)
        REFERENCES reliability_scenario_suites(workspace_id, suite_id),
    FOREIGN KEY(mis_task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(mis_plan_id) REFERENCES agent_plans(plan_id)
);

CREATE TABLE IF NOT EXISTS reliability_conversation_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    campaign_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    agent_version_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mis_run_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, run_id),
    FOREIGN KEY(workspace_id, campaign_id)
        REFERENCES reliability_campaigns(workspace_id, campaign_id),
    FOREIGN KEY(workspace_id, scenario_id)
        REFERENCES reliability_scenarios(workspace_id, scenario_id),
    FOREIGN KEY(workspace_id, agent_version_id)
        REFERENCES reliability_agent_versions(workspace_id, agent_version_id),
    FOREIGN KEY(mis_run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS reliability_conversation_turns (
    workspace_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    run_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL CHECK(turn_index >= 0),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, turn_id),
    UNIQUE(workspace_id, run_id, turn_index),
    FOREIGN KEY(workspace_id, run_id)
        REFERENCES reliability_conversation_runs(workspace_id, run_id)
);

CREATE TABLE IF NOT EXISTS reliability_observed_tool_calls (
    workspace_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    run_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    error TEXT,
    is_mutation INTEGER NOT NULL CHECK(is_mutation IN (0, 1)),
    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
    mis_tool_call_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, tool_call_id),
    FOREIGN KEY(workspace_id, run_id)
        REFERENCES reliability_conversation_runs(workspace_id, run_id),
    FOREIGN KEY(workspace_id, turn_id)
        REFERENCES reliability_conversation_turns(workspace_id, turn_id),
    FOREIGN KEY(mis_tool_call_id) REFERENCES tool_calls(tool_call_id)
);

CREATE TABLE IF NOT EXISTS reliability_evaluation_results (
    workspace_id TEXT NOT NULL,
    evaluation_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    run_id TEXT NOT NULL,
    evaluator_id TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL,
    threshold REAL,
    reason_codes_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    mis_evaluation_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, evaluation_id),
    FOREIGN KEY(workspace_id, run_id)
        REFERENCES reliability_conversation_runs(workspace_id, run_id),
    FOREIGN KEY(mis_evaluation_id) REFERENCES evaluations(evaluation_id)
);

CREATE TABLE IF NOT EXISTS reliability_failures (
    workspace_id TEXT NOT NULL,
    failure_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    run_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    evaluation_result_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, failure_id),
    FOREIGN KEY(workspace_id, run_id)
        REFERENCES reliability_conversation_runs(workspace_id, run_id),
    FOREIGN KEY(workspace_id, scenario_id)
        REFERENCES reliability_scenarios(workspace_id, scenario_id),
    FOREIGN KEY(workspace_id, evaluation_result_id)
        REFERENCES reliability_evaluation_results(workspace_id, evaluation_id)
);

CREATE TABLE IF NOT EXISTS reliability_failure_clusters (
    workspace_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    campaign_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, cluster_id),
    FOREIGN KEY(workspace_id, campaign_id)
        REFERENCES reliability_campaigns(workspace_id, campaign_id)
);

CREATE TABLE IF NOT EXISTS reliability_failure_cluster_members (
    workspace_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    failure_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    PRIMARY KEY(workspace_id, cluster_id, failure_id),
    UNIQUE(workspace_id, cluster_id, ordinal),
    FOREIGN KEY(workspace_id, cluster_id)
        REFERENCES reliability_failure_clusters(workspace_id, cluster_id),
    FOREIGN KEY(workspace_id, failure_id)
        REFERENCES reliability_failures(workspace_id, failure_id)
);

CREATE TABLE IF NOT EXISTS reliability_regressions (
    workspace_id TEXT NOT NULL,
    regression_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    failure_case_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    original_input_json TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evaluator_id TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    mis_memory_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, regression_id),
    FOREIGN KEY(workspace_id, failure_case_id)
        REFERENCES reliability_failures(workspace_id, failure_id),
    FOREIGN KEY(workspace_id, scenario_id)
        REFERENCES reliability_scenarios(workspace_id, scenario_id),
    FOREIGN KEY(workspace_id, source_run_id)
        REFERENCES reliability_conversation_runs(workspace_id, run_id),
    FOREIGN KEY(mis_memory_id) REFERENCES memories(memory_id)
);

CREATE TABLE IF NOT EXISTS reliability_release_gates (
    workspace_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    campaign_id TEXT NOT NULL,
    baseline_campaign_id TEXT,
    decision TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    mis_approval_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, gate_id),
    FOREIGN KEY(workspace_id, campaign_id)
        REFERENCES reliability_campaigns(workspace_id, campaign_id),
    FOREIGN KEY(workspace_id, baseline_campaign_id)
        REFERENCES reliability_campaigns(workspace_id, campaign_id),
    FOREIGN KEY(mis_approval_id) REFERENCES approvals(approval_id)
);

CREATE TABLE IF NOT EXISTS reliability_evidence_manifests (
    workspace_id TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
    campaign_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    mis_artifact_id TEXT,
    mis_plan_evidence_manifest_id TEXT,
    git_commit_sha TEXT NOT NULL,
    environment_json TEXT NOT NULL,
    scenario_sha256 TEXT NOT NULL,
    agent_config_sha256 TEXT NOT NULL,
    evaluator_versions_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    final_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, manifest_id),
    FOREIGN KEY(workspace_id, campaign_id)
        REFERENCES reliability_campaigns(workspace_id, campaign_id),
    FOREIGN KEY(workspace_id, run_id)
        REFERENCES reliability_conversation_runs(workspace_id, run_id),
    FOREIGN KEY(mis_artifact_id) REFERENCES artifacts(artifact_id),
    FOREIGN KEY(mis_plan_evidence_manifest_id)
        REFERENCES plan_evidence_manifests(manifest_id)
);

CREATE INDEX IF NOT EXISTS idx_reliability_agents_workspace
    ON reliability_agents(workspace_id, created_at, agent_id);
CREATE INDEX IF NOT EXISTS idx_reliability_scenarios_suite
    ON reliability_scenarios(workspace_id, suite_id, created_at, scenario_id);
CREATE INDEX IF NOT EXISTS idx_reliability_campaigns_workspace
    ON reliability_campaigns(workspace_id, created_at DESC, campaign_id);
CREATE INDEX IF NOT EXISTS idx_reliability_runs_campaign
    ON reliability_conversation_runs(workspace_id, campaign_id, created_at, run_id);
CREATE INDEX IF NOT EXISTS idx_reliability_turns_run
    ON reliability_conversation_turns(workspace_id, run_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_reliability_calls_run
    ON reliability_observed_tool_calls(workspace_id, run_id, created_at, tool_call_id);
CREATE INDEX IF NOT EXISTS idx_reliability_evaluations_run
    ON reliability_evaluation_results(workspace_id, run_id, created_at, evaluation_id);
CREATE INDEX IF NOT EXISTS idx_reliability_failures_run
    ON reliability_failures(workspace_id, run_id, created_at, failure_id);
CREATE INDEX IF NOT EXISTS idx_reliability_regressions_run
    ON reliability_regressions(workspace_id, source_run_id, created_at, regression_id);
CREATE INDEX IF NOT EXISTS idx_reliability_gates_campaign
    ON reliability_release_gates(workspace_id, campaign_id, created_at, gate_id);
CREATE INDEX IF NOT EXISTS idx_reliability_manifests_run
    ON reliability_evidence_manifests(workspace_id, run_id, created_at, manifest_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_campaign_mis_task
    ON reliability_campaigns(mis_task_id) WHERE mis_task_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_campaign_mis_plan
    ON reliability_campaigns(mis_plan_id) WHERE mis_plan_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_run_mis_run
    ON reliability_conversation_runs(mis_run_id) WHERE mis_run_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_tool_call_mis_tool_call
    ON reliability_observed_tool_calls(mis_tool_call_id)
    WHERE mis_tool_call_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_evaluation_mis_evaluation
    ON reliability_evaluation_results(mis_evaluation_id)
    WHERE mis_evaluation_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_regression_mis_memory
    ON reliability_regressions(mis_memory_id) WHERE mis_memory_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_gate_mis_approval
    ON reliability_release_gates(mis_approval_id)
    WHERE mis_approval_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_manifest_mis_artifact
    ON reliability_evidence_manifests(mis_artifact_id)
    WHERE mis_artifact_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reliability_manifest_mis_plan_evidence
    ON reliability_evidence_manifests(mis_plan_evidence_manifest_id)
    WHERE mis_plan_evidence_manifest_id IS NOT NULL;
"""


_AUTHORITY_SQL = {
    "task": """SELECT t.*, t.workspace_id AS authority_workspace_id
        FROM tasks t WHERE t.task_id=?""",
    "plan": """SELECT p.*, p.workspace_id AS authority_workspace_id
        FROM agent_plans p WHERE p.plan_id=?""",
    "run": """SELECT r.*, r.workspace_id AS authority_workspace_id
        FROM runs r WHERE r.run_id=?""",
    "tool_call": """SELECT tc.*, r.workspace_id AS authority_workspace_id
        FROM tool_calls tc JOIN runs r ON r.run_id=tc.run_id
        WHERE tc.tool_call_id=?""",
    "evaluation": """SELECT e.*, r.workspace_id AS authority_workspace_id
        FROM evaluations e JOIN runs r ON r.run_id=e.run_id
        WHERE e.evaluation_id=?""",
    "artifact": """SELECT a.*, COALESCE(r.workspace_id,t.workspace_id) AS authority_workspace_id,
            r.workspace_id AS run_workspace_id, t.workspace_id AS task_workspace_id
        FROM artifacts a
        LEFT JOIN runs r ON r.run_id=a.run_id
        LEFT JOIN tasks t ON t.task_id=a.task_id
        WHERE a.artifact_id=?""",
    "approval": """SELECT a.*, r.workspace_id AS authority_workspace_id
        FROM approvals a JOIN runs r ON r.run_id=a.run_id
        WHERE a.approval_id=?""",
    "memory": """SELECT m.*, m.workspace_id AS authority_workspace_id
        FROM memories m WHERE m.memory_id=?""",
    "plan_evidence_manifest": """SELECT m.*, m.workspace_id AS authority_workspace_id
        FROM plan_evidence_manifests m WHERE m.manifest_id=?""",
}


class SQLiteRepository:
    """Workspace-scoped projection repository over a caller-owned connection."""

    _savepoint_counter = itertools.count()

    def __init__(self, conn: sqlite3.Connection, *, workspace_id: str):
        if not isinstance(conn, sqlite3.Connection):
            raise TypeError("conn must be sqlite3.Connection")
        if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(
            workspace_id
        ):
            raise ValueError("workspace_id must be a bounded opaque identifier")
        self.conn = conn
        self.workspace_id = workspace_id

    def initialize_schema(self) -> None:
        foreign_keys = int(self.conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if not foreign_keys:
            if self.conn.in_transaction:
                raise RepositoryError(
                    "foreign keys must be enabled before starting a transaction"
                )
            self.conn.execute("PRAGMA foreign_keys=ON")
            if not int(self.conn.execute("PRAGMA foreign_keys").fetchone()[0]):
                raise RepositoryError("SQLite foreign key enforcement is required")
        self._validate_authority_schema()
        for statement in RELIABILITY_SCHEMA_SQL.split(";"):
            statement = statement.strip()
            if statement:
                self.conn.execute(statement)

    def _validate_authority_schema(self) -> None:
        missing: list[str] = []
        for table, required_columns in _AUTHORITY_COLUMNS.items():
            columns = {
                str(row[1])
                for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            absent = sorted(required_columns - columns)
            if absent:
                missing.append(f"{table}({','.join(absent)})")
        if missing:
            raise RepositoryError(
                "MIS authority schema is unavailable or incompatible: "
                + "; ".join(missing)
            )

    def upsert_agent(self, agent: AgentUnderTest) -> str:
        self._expect_model(agent, AgentUnderTest, "agent")
        if agent.workspace_id is not None and agent.workspace_id != self.workspace_id:
            raise RepositoryConflictError("agent belongs to another workspace")
        data = agent.model_dump(mode="json")
        return self._upsert(
            "reliability_agents",
            "agent_id",
            {
                "workspace_id": self.workspace_id,
                "agent_id": data["id"],
                "schema_version": data["schema_version"],
                "name": self._safe_text(data["name"], "agent.name"),
                "description": self._safe_text(
                    data["description"], "agent.description"
                ),
                "created_at": data["created_at"],
            },
            immutable={"schema_version", "created_at"},
        )

    def upsert_agent_version(self, version: AgentVersion) -> str:
        self._expect_model(version, AgentVersion, "version")
        self._require_vertical("reliability_agents", "agent_id", version.agent_id)
        data = version.model_dump(mode="json")
        return self._upsert(
            "reliability_agent_versions",
            "agent_version_id",
            {
                "workspace_id": self.workspace_id,
                "agent_version_id": data["id"],
                "schema_version": data["schema_version"],
                "agent_id": data["agent_id"],
                "version": data["version"],
                "adapter_kind": data["adapter_kind"],
                "config_sha256": data["config_sha256"],
                "created_at": data["created_at"],
            },
            immutable={"schema_version", "agent_id", "created_at"},
        )

    def upsert_scenario_suite(self, suite: ScenarioSuite) -> str:
        self._expect_model(suite, ScenarioSuite, "suite")
        data = suite.model_dump(mode="json")
        return self._upsert(
            "reliability_scenario_suites",
            "suite_id",
            {
                "workspace_id": self.workspace_id,
                "suite_id": data["id"],
                "schema_version": data["schema_version"],
                "name": self._safe_text(data["name"], "suite.name"),
                "description": self._safe_text(
                    data["description"], "suite.description"
                ),
                "created_at": data["created_at"],
            },
            immutable={"schema_version", "created_at"},
        )

    def upsert_persona(self, persona: Persona) -> str:
        self._expect_model(persona, Persona, "persona")
        data = persona.model_dump(mode="json")
        return self._upsert(
            "reliability_personas",
            "persona_id",
            {
                "workspace_id": self.workspace_id,
                "persona_id": data["id"],
                "schema_version": data["schema_version"],
                "name": self._safe_text(data["name"], "persona.name"),
                "language": self._safe_text(data["language"], "persona.language"),
                "tone": self._safe_text(data["tone"], "persona.tone"),
                "verbosity": data["verbosity"],
                "created_at": data["created_at"],
            },
            immutable={"schema_version", "created_at"},
        )

    def upsert_scenario(
        self,
        scenario: Scenario,
        *,
        contract: ScenarioDefinition,
    ) -> str:
        self._expect_model(scenario, Scenario, "scenario")
        self._expect_model(contract, ScenarioDefinition, "contract")
        self._require_vertical(
            "reliability_scenario_suites", "suite_id", scenario.suite_id
        )
        persona_row = self._require_vertical(
            "reliability_personas", "persona_id", scenario.persona_id
        )
        if (
            contract.id != scenario.id
            or contract.name != scenario.name
            or contract.initial_message != scenario.initial_message
            or contract.goal.type.value != scenario.goal_type
        ):
            raise RepositoryConflictError(
                "ScenarioDefinition does not match the persisted Scenario identity"
            )
        if (
            persona_row["language"] != contract.persona.language
            or persona_row["tone"] != contract.persona.tone
            or persona_row["verbosity"] != contract.persona.verbosity.value
        ):
            raise RepositoryConflictError(
                "ScenarioDefinition persona does not match persona_id"
            )
        data = scenario.model_dump(mode="json")
        contract_data = contract.model_dump(mode="json")
        return self._upsert(
            "reliability_scenarios",
            "scenario_id",
            {
                "workspace_id": self.workspace_id,
                "scenario_id": data["id"],
                "schema_version": data["schema_version"],
                "suite_id": data["suite_id"],
                "persona_id": data["persona_id"],
                "name": self._safe_text(data["name"], "scenario.name"),
                "initial_message": self._safe_text(
                    data["initial_message"], "scenario.initial_message"
                ),
                "goal_type": data["goal_type"],
                "source_sha256": data["source_sha256"],
                "persona_json": self._json(
                    self._safe_payload(contract_data["persona"], "persona"),
                    "persona",
                ),
                "goal_json": self._json(
                    self._safe_payload(contract_data["goal"], "goal"), "goal"
                ),
                "challenges_json": self._json(
                    self._safe_payload(contract_data["challenges"], "challenges"),
                    "challenges",
                ),
                "expectations_json": self._json(
                    self._safe_payload(contract_data["expectations"], "expectations"),
                    "expectations",
                ),
                "tags_json": self._json(
                    self._safe_payload(contract_data["tags"], "tags"), "tags"
                ),
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "suite_id",
                "persona_id",
                "created_at",
            },
        )

    def upsert_campaign(self, campaign: Campaign) -> str:
        self._expect_model(campaign, Campaign, "campaign")
        self._require_vertical(
            "reliability_agent_versions",
            "agent_version_id",
            campaign.agent_version_id,
        )
        self._require_vertical(
            "reliability_scenario_suites",
            "suite_id",
            campaign.scenario_suite_id,
        )
        if (campaign.mis_task_id is None) != (campaign.mis_plan_id is None):
            raise AuthorityMappingError(
                "campaign must map its MIS task and plan together"
            )
        task = self._authority("task", campaign.mis_task_id)
        plan = self._authority("plan", campaign.mis_plan_id)
        if task and plan and plan.get("task_id") != task.get("task_id"):
            raise AuthorityMappingError("MIS plan is not linked to the mapped task")
        data = campaign.model_dump(mode="json")
        return self._upsert(
            "reliability_campaigns",
            "campaign_id",
            {
                "workspace_id": self.workspace_id,
                "campaign_id": data["id"],
                "schema_version": data["schema_version"],
                "agent_version_id": data["agent_version_id"],
                "scenario_suite_id": data["scenario_suite_id"],
                "status": data["status"],
                "mis_task_id": data["mis_task_id"],
                "mis_plan_id": data["mis_plan_id"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "agent_version_id",
                "scenario_suite_id",
                "created_at",
            },
            mutable={"status"},
        )

    def upsert_run_projection(
        self,
        run: ConversationRun,
        *,
        turns: Sequence[ConversationTurn],
        tool_calls: Sequence[ObservedToolCall],
        evaluations: Sequence[EvaluationResult],
    ) -> str:
        self._expect_model(run, ConversationRun, "run")
        campaign = self._require_vertical(
            "reliability_campaigns", "campaign_id", run.campaign_id
        )
        scenario = self._require_vertical(
            "reliability_scenarios", "scenario_id", run.scenario_id
        )
        self._require_vertical(
            "reliability_agent_versions", "agent_version_id", run.agent_version_id
        )
        if run.agent_version_id != campaign["agent_version_id"]:
            raise RepositoryConflictError(
                "run agent_version_id does not match the campaign agent version"
            )
        if scenario["suite_id"] != campaign["scenario_suite_id"]:
            raise RepositoryConflictError(
                "run scenario does not belong to the campaign scenario suite"
            )
        authority_run = self._authority("run", run.mis_run_id)
        campaign_has_authority = bool(
            campaign.get("mis_task_id") and campaign.get("mis_plan_id")
        )
        if bool(run.mis_run_id) != campaign_has_authority:
            raise AuthorityMappingError(
                "MIS run mapping must match the campaign authority mode"
            )
        if authority_run and authority_run.get("task_id") != campaign["mis_task_id"]:
            raise AuthorityMappingError("MIS run is not linked to the campaign task")
        if (
            authority_run
            and "agent_plan_id" in authority_run
            and authority_run.get("agent_plan_id") != campaign["mis_plan_id"]
        ):
            raise AuthorityMappingError("MIS run is not linked to the campaign plan")

        turn_ids: set[str] = set()
        turn_indexes: set[int] = set()
        for turn in turns:
            self._expect_model(turn, ConversationTurn, "turn")
            if turn.run_id != run.id:
                raise RepositoryConflictError("turn belongs to another run")
            if turn.id in turn_ids or turn.turn_index in turn_indexes:
                raise RepositoryConflictError("turn IDs and indexes must be unique")
            turn_ids.add(turn.id)
            turn_indexes.add(turn.turn_index)
        for call in tool_calls:
            self._expect_model(call, ObservedToolCall, "tool_call")
            if call.run_id != run.id or call.turn_id not in turn_ids:
                raise RepositoryConflictError(
                    "tool call has an invalid run/turn parent"
                )
            if call.mis_tool_call_id is not None and run.mis_run_id is None:
                raise AuthorityMappingError(
                    "MIS tool call mapping requires a mapped MIS run"
                )
            if campaign_has_authority and call.mis_tool_call_id is None:
                raise AuthorityMappingError(
                    "MIS tool call mapping is required for a governed run"
                )
            authority_call = self._authority("tool_call", call.mis_tool_call_id)
            if (
                authority_call
                and run.mis_run_id
                and authority_call.get("run_id") != run.mis_run_id
            ):
                raise AuthorityMappingError(
                    "MIS tool call is not linked to the mapped MIS run"
                )
            if (
                authority_call
                and "tool_name" in authority_call
                and authority_call.get("tool_name") != call.name
            ):
                raise AuthorityMappingError(
                    "MIS tool call name does not match the observed tool call"
                )
            if (
                authority_call
                and authority_run
                and authority_call.get("agent_id") != authority_run.get("agent_id")
            ):
                raise AuthorityMappingError(
                    "MIS tool call agent does not match the mapped MIS run agent"
                )
        for evaluation in evaluations:
            self._expect_model(evaluation, EvaluationResult, "evaluation")
            if evaluation.run_id != run.id:
                raise RepositoryConflictError("evaluation belongs to another run")
            if evaluation.mis_evaluation_id is not None and run.mis_run_id is None:
                raise AuthorityMappingError(
                    "MIS evaluation mapping requires a mapped MIS run"
                )
            if (
                evaluation.status.value == "skipped"
                and evaluation.mis_evaluation_id is not None
            ):
                raise AuthorityMappingError(
                    "SKIPPED vertical evaluations must not claim a scored MIS evaluation"
                )
            if (
                campaign_has_authority
                and evaluation.status.value != "skipped"
                and evaluation.mis_evaluation_id is None
            ):
                raise AuthorityMappingError(
                    "MIS evaluation mapping is required for a governed run"
                )
            authority_evaluation = self._authority(
                "evaluation", evaluation.mis_evaluation_id
            )
            if (
                authority_evaluation
                and run.mis_run_id
                and authority_evaluation.get("run_id") != run.mis_run_id
            ):
                raise AuthorityMappingError(
                    "MIS evaluation is not linked to the mapped MIS run"
                )
            expected_score = (
                0.0 if evaluation.status.value == "error" else evaluation.score
            )
            if authority_evaluation and expected_score is not None:
                authority_score = authority_evaluation.get("score")
                if authority_score is None or (
                    abs(float(authority_score) - expected_score) > 1e-12
                ):
                    raise AuthorityMappingError(
                        "MIS evaluation error score must be 0.0"
                        if evaluation.status.value == "error"
                        else "MIS evaluation score does not match the vertical evaluation"
                    )
            if authority_evaluation and authority_evaluation.get(
                "task_id"
            ) != campaign.get("mis_task_id"):
                raise AuthorityMappingError(
                    "MIS evaluation is not linked to the campaign task"
                )
            if (
                authority_evaluation
                and authority_run
                and authority_evaluation.get("agent_id")
                != authority_run.get("agent_id")
            ):
                raise AuthorityMappingError(
                    "MIS evaluation agent does not match the mapped MIS run agent"
                )
            expected_pass_fail = {
                "pass": "pass",
                "fail": "fail",
                "warn": "fail",
                "error": "fail",
            }.get(evaluation.status.value)
            if (
                authority_evaluation
                and expected_pass_fail is not None
                and authority_evaluation.get("pass_fail") != expected_pass_fail
            ):
                raise AuthorityMappingError(
                    "MIS evaluation status does not match the vertical evaluation"
                )
            if authority_evaluation and "rubric_json" in authority_evaluation:
                rubric = self._decode(
                    authority_evaluation["rubric_json"], "MIS evaluation rubric_json"
                )
                if not isinstance(rubric, dict):
                    raise AuthorityMappingError(
                        "MIS evaluation rubric_json must be an object"
                    )
                authority_evaluator_id = rubric.get("evaluator_id")
                if authority_evaluator_id != evaluation.evaluator_id:
                    raise AuthorityMappingError(
                        "MIS evaluation rubric evaluator identity is missing or inconsistent"
                    )
                if rubric.get("vertical_status") != evaluation.status.value:
                    raise AuthorityMappingError(
                        "MIS evaluation rubric vertical status is missing or inconsistent"
                    )

        with self._savepoint():
            outcome = self._upsert_run(run)
            for turn in turns:
                self._upsert_turn(turn)
            for call in tool_calls:
                self._upsert_tool_call(call)
            for evaluation in evaluations:
                self._upsert_evaluation(evaluation)
        return outcome

    def upsert_failure(self, failure: FailureCase) -> str:
        self._expect_model(failure, FailureCase, "failure")
        run = self._require_vertical(
            "reliability_conversation_runs", "run_id", failure.run_id
        )
        self._require_vertical(
            "reliability_scenarios", "scenario_id", failure.scenario_id
        )
        evaluation = self._require_vertical(
            "reliability_evaluation_results",
            "evaluation_id",
            failure.evaluation_result_id,
        )
        if run["scenario_id"] != failure.scenario_id:
            raise RepositoryConflictError(
                "failure scenario does not match its source run"
            )
        if evaluation["run_id"] != failure.run_id:
            raise RepositoryConflictError(
                "failure evaluation does not belong to its source run"
            )
        data = failure.model_dump(mode="json")
        return self._upsert(
            "reliability_failures",
            "failure_id",
            {
                "workspace_id": self.workspace_id,
                "failure_id": data["id"],
                "schema_version": data["schema_version"],
                "run_id": data["run_id"],
                "scenario_id": data["scenario_id"],
                "evaluation_result_id": data["evaluation_result_id"],
                "reason_code": data["reason_code"],
                "expected_json": self._json(
                    self._safe_payload(data["expected"], "failure.expected"),
                    "expected",
                ),
                "observed_json": self._json(
                    self._safe_payload(data["observed"], "failure.observed"),
                    "observed",
                ),
                "evidence_refs_json": self._json(
                    self._safe_payload(data["evidence_refs"], "failure.evidence_refs"),
                    "evidence_refs",
                ),
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "run_id",
                "scenario_id",
                "evaluation_result_id",
                "created_at",
            },
        )

    def upsert_failure_cluster(self, cluster: FailureCluster) -> str:
        self._expect_model(cluster, FailureCluster, "cluster")
        self._require_vertical(
            "reliability_campaigns", "campaign_id", cluster.campaign_id
        )
        for failure_id in cluster.failure_case_ids:
            failure = self._require_vertical(
                "reliability_failures", "failure_id", failure_id
            )
            run = self._require_vertical(
                "reliability_conversation_runs", "run_id", failure["run_id"]
            )
            if run["campaign_id"] != cluster.campaign_id:
                raise RepositoryConflictError(
                    "cluster failure belongs to another campaign"
                )
        data = cluster.model_dump(mode="json")
        existing_cluster = self._fetchone(
            """SELECT cluster_id FROM reliability_failure_clusters
            WHERE workspace_id=? AND cluster_id=?""",
            (self.workspace_id, cluster.id),
        )
        existing_members = [
            row["failure_id"]
            for row in self._fetchall(
                """SELECT failure_id FROM reliability_failure_cluster_members
                WHERE workspace_id=? AND cluster_id=? ORDER BY ordinal""",
                (self.workspace_id, cluster.id),
            )
        ]
        if (
            existing_cluster is not None
            and existing_members != cluster.failure_case_ids
        ):
            raise RepositoryConflictError(
                "stable failure cluster IDs cannot rebind cluster members"
            )
        with self._savepoint():
            outcome = self._upsert(
                "reliability_failure_clusters",
                "cluster_id",
                {
                    "workspace_id": self.workspace_id,
                    "cluster_id": data["id"],
                    "schema_version": data["schema_version"],
                    "campaign_id": data["campaign_id"],
                    "signature": data["signature"],
                    "created_at": data["created_at"],
                },
                immutable={"schema_version", "campaign_id", "created_at"},
            )
            if existing_cluster is None:
                self.conn.executemany(
                    """INSERT INTO reliability_failure_cluster_members(
                        workspace_id,cluster_id,failure_id,ordinal
                    ) VALUES(?,?,?,?)""",
                    [
                        (self.workspace_id, cluster.id, failure_id, ordinal)
                        for ordinal, failure_id in enumerate(cluster.failure_case_ids)
                    ],
                )
        return outcome

    def upsert_regression(self, regression: RegressionCase) -> str:
        self._expect_model(regression, RegressionCase, "regression")
        failure = self._require_vertical(
            "reliability_failures", "failure_id", regression.failure_case_id
        )
        self._require_vertical(
            "reliability_scenarios", "scenario_id", regression.scenario_id
        )
        source_run = self._require_vertical(
            "reliability_conversation_runs", "run_id", regression.source_run_id
        )
        if failure["run_id"] != regression.source_run_id:
            raise RepositoryConflictError(
                "regression failure source run does not match source_run_id"
            )
        if failure["scenario_id"] != regression.scenario_id:
            raise RepositoryConflictError(
                "regression failure scenario does not match scenario_id"
            )
        evaluation = self._require_vertical(
            "reliability_evaluation_results",
            "evaluation_id",
            failure["evaluation_result_id"],
        )
        if evaluation["evaluator_id"] != regression.evaluator_id:
            raise RepositoryConflictError(
                "regression evaluator does not match the source failure"
            )
        if failure["reason_code"] != regression.reason_code:
            raise RepositoryConflictError(
                "regression reason does not match the source failure"
            )
        campaign = self._require_vertical(
            "reliability_campaigns", "campaign_id", source_run["campaign_id"]
        )
        campaign_has_authority = bool(
            campaign.get("mis_task_id") and campaign.get("mis_plan_id")
        )
        if bool(regression.mis_memory_id) != campaign_has_authority:
            raise AuthorityMappingError(
                "MIS memory mapping is required exactly for governed regressions"
            )
        memory = self._authority("memory", regression.mis_memory_id)
        if memory and memory.get("memory_type") != "failure_case":
            raise AuthorityMappingError(
                "MIS regression memory must use memory_type='failure_case'"
            )
        if memory and memory.get("task_id") != campaign.get("mis_task_id"):
            raise AuthorityMappingError("MIS memory is not linked to the campaign task")
        if memory and memory.get("source_ref") != source_run.get("mis_run_id"):
            raise AuthorityMappingError(
                "MIS regression memory is not linked to the source run"
            )
        if memory and memory.get("review_status") != "candidate":
            raise AuthorityMappingError("MIS regression memory must remain a candidate")
        data = regression.model_dump(mode="json")
        return self._upsert(
            "reliability_regressions",
            "regression_id",
            {
                "workspace_id": self.workspace_id,
                "regression_id": data["id"],
                "schema_version": data["schema_version"],
                "failure_case_id": data["failure_case_id"],
                "scenario_id": data["scenario_id"],
                "source_run_id": data["source_run_id"],
                "name": self._safe_text(data["name"], "regression.name"),
                "original_input_json": self._json(
                    self._safe_payload(
                        data["original_input"], "regression.original_input"
                    ),
                    "original_input",
                ),
                "expected_json": self._json(
                    self._safe_payload(data["expected"], "regression.expected"),
                    "expected",
                ),
                "observed_json": self._json(
                    self._safe_payload(data["observed"], "regression.observed"),
                    "observed",
                ),
                "reason_code": data["reason_code"],
                "evaluator_id": data["evaluator_id"],
                "evidence_refs_json": self._json(
                    self._safe_payload(
                        data["evidence_refs"], "regression.evidence_refs"
                    ),
                    "evidence_refs",
                ),
                "mis_memory_id": data["mis_memory_id"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "failure_case_id",
                "scenario_id",
                "source_run_id",
                "created_at",
            },
        )

    def upsert_release_gate(self, gate: ReleaseGateDecision) -> str:
        self._expect_model(gate, ReleaseGateDecision, "gate")
        campaign = self._require_vertical(
            "reliability_campaigns", "campaign_id", gate.campaign_id
        )
        if gate.baseline_campaign_id is not None:
            self._require_vertical(
                "reliability_campaigns",
                "campaign_id",
                gate.baseline_campaign_id,
            )
        campaign_has_authority = bool(
            campaign.get("mis_task_id") and campaign.get("mis_plan_id")
        )
        if bool(gate.mis_approval_id) != campaign_has_authority:
            raise AuthorityMappingError(
                "MIS approval mapping is required exactly for governed release gates"
            )
        approval = self._authority("approval", gate.mis_approval_id)
        if approval:
            if (
                campaign.get("mis_task_id")
                and approval.get("task_id") != campaign["mis_task_id"]
            ):
                raise AuthorityMappingError(
                    "MIS approval is not linked to the campaign task"
                )
            mapped_runs = self._fetchall(
                """SELECT mis_run_id FROM reliability_conversation_runs
                WHERE workspace_id=? AND campaign_id=? AND mis_run_id IS NOT NULL""",
                (self.workspace_id, gate.campaign_id),
            )
            if not mapped_runs:
                raise AuthorityMappingError(
                    "MIS approval mapping requires a mapped campaign run"
                )
            if approval.get("run_id") not in {row["mis_run_id"] for row in mapped_runs}:
                raise AuthorityMappingError(
                    "MIS approval is not linked to a campaign run"
                )
            if (
                approval.get("subject_type") != "reliability_release_gate"
                or approval.get("subject_id") != gate.id
            ):
                raise AuthorityMappingError(
                    "MIS approval does not identify the reliability release gate subject"
                )
            expected_decision = {
                "pass": "approved",
                "warn": "approved",
                "block": "rejected",
            }[gate.decision.value]
            if approval.get("decision") != expected_decision:
                raise AuthorityMappingError(
                    "MIS approval decision does not match the release gate decision"
                )
        data = gate.model_dump(mode="json")
        return self._upsert(
            "reliability_release_gates",
            "gate_id",
            {
                "workspace_id": self.workspace_id,
                "gate_id": data["id"],
                "schema_version": data["schema_version"],
                "campaign_id": data["campaign_id"],
                "baseline_campaign_id": data["baseline_campaign_id"],
                "decision": data["decision"],
                "policy_version": data["policy_version"],
                "blockers_json": self._json(
                    self._safe_payload(data["blockers"], "gate.blockers"),
                    "blockers",
                ),
                "warnings_json": self._json(
                    self._safe_payload(data["warnings"], "gate.warnings"),
                    "warnings",
                ),
                "metrics_json": self._json(
                    self._safe_payload(data["metrics"], "gate.metrics"), "metrics"
                ),
                "evidence_refs_json": self._json(
                    self._safe_payload(data["evidence_refs"], "gate.evidence_refs"),
                    "evidence_refs",
                ),
                "mis_approval_id": data["mis_approval_id"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "campaign_id",
                "baseline_campaign_id",
                "created_at",
            },
        )

    def upsert_evidence_manifest(self, manifest: EvidenceManifest) -> str:
        self._expect_model(manifest, EvidenceManifest, "manifest")
        campaign = self._require_vertical(
            "reliability_campaigns", "campaign_id", manifest.campaign_id
        )
        run = self._require_vertical(
            "reliability_conversation_runs", "run_id", manifest.run_id
        )
        if run["campaign_id"] != manifest.campaign_id:
            raise RepositoryConflictError("manifest run belongs to another campaign")
        campaign_has_authority = bool(
            campaign.get("mis_task_id") and campaign.get("mis_plan_id")
        )
        if bool(manifest.mis_artifact_id) != campaign_has_authority:
            raise AuthorityMappingError(
                "MIS artifact mapping is required exactly for governed evidence"
            )
        if (
            manifest.mis_plan_evidence_manifest_id is not None
            and not campaign_has_authority
        ):
            raise AuthorityMappingError(
                "MIS plan evidence mapping requires governed campaign authority"
            )
        artifact = self._authority("artifact", manifest.mis_artifact_id)
        if artifact:
            if artifact.get("artifact_type") not in {
                "evidence",
                "open_cekura_evidence",
                "reliability_evidence",
            }:
                raise AuthorityMappingError(
                    "MIS artifact type is not reliability evidence"
                )
            if run.get("mis_run_id") and artifact.get("run_id") not in {
                None,
                run["mis_run_id"],
            }:
                raise AuthorityMappingError(
                    "MIS artifact is not linked to the manifest run"
                )
            if campaign.get("mis_task_id") and artifact.get("task_id") not in {
                None,
                campaign["mis_task_id"],
            }:
                raise AuthorityMappingError(
                    "MIS artifact is not linked to the campaign task"
                )
        plan_manifest = self._authority(
            "plan_evidence_manifest", manifest.mis_plan_evidence_manifest_id
        )
        if plan_manifest:
            if plan_manifest.get("status") != "verified":
                raise AuthorityMappingError(
                    "MIS plan evidence manifest is not verified"
                )
            artifact_ids_json = plan_manifest.get("artifact_ids_json")
            if not isinstance(artifact_ids_json, str):
                raise AuthorityMappingError(
                    "MIS plan evidence artifact_ids_json is unavailable"
                )
            try:
                artifact_ids = self._decode(
                    artifact_ids_json,
                    "MIS plan evidence artifact_ids_json",
                )
            except RepositoryError as exc:
                raise AuthorityMappingError(
                    "MIS plan evidence artifact_ids_json is invalid"
                ) from exc
            if (
                not isinstance(artifact_ids, list)
                or manifest.mis_artifact_id not in artifact_ids
            ):
                raise AuthorityMappingError(
                    "MIS plan evidence does not contain the declared artifact"
                )
            expected = {
                "plan_id": campaign.get("mis_plan_id"),
                "task_id": campaign.get("mis_task_id"),
                "run_id": run.get("mis_run_id"),
            }
            for column, expected_value in expected.items():
                if expected_value and plan_manifest.get(column) != expected_value:
                    raise AuthorityMappingError(
                        f"MIS plan evidence manifest has inconsistent {column}"
                    )
        data = manifest.model_dump(mode="json")
        return self._upsert(
            "reliability_evidence_manifests",
            "manifest_id",
            {
                "workspace_id": self.workspace_id,
                "manifest_id": data["id"],
                "schema_version": data["schema_version"],
                "campaign_id": data["campaign_id"],
                "run_id": data["run_id"],
                "mis_artifact_id": data["mis_artifact_id"],
                "mis_plan_evidence_manifest_id": data["mis_plan_evidence_manifest_id"],
                "git_commit_sha": data["git_commit_sha"],
                "environment_json": self._json(
                    self._safe_payload(data["environment"], "manifest.environment"),
                    "environment",
                ),
                "scenario_sha256": data["scenario_sha256"],
                "agent_config_sha256": data["agent_config_sha256"],
                "evaluator_versions_json": self._json(
                    data["evaluator_versions"], "evaluator_versions"
                ),
                "artifacts_json": self._json(data["artifacts"], "artifacts"),
                "started_at": data["started_at"],
                "finished_at": data["finished_at"],
                "final_state": data["final_state"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "campaign_id",
                "run_id",
                "git_commit_sha",
                "created_at",
            },
        )

    def list_agents(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        return self._public_tree(
            self._fetchall(
                """SELECT a.*,
                (SELECT COUNT(*) FROM reliability_agent_versions v
                 WHERE v.workspace_id=a.workspace_id AND v.agent_id=a.agent_id)
                 AS version_count
            FROM reliability_agents a WHERE a.workspace_id=?
            ORDER BY a.created_at,a.agent_id LIMIT ? OFFSET ?""",
                (self.workspace_id, limit, offset),
            )
        )

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        agent = self._fetchone(
            "SELECT * FROM reliability_agents WHERE workspace_id=? AND agent_id=?",
            (self.workspace_id, agent_id),
        )
        if agent is None:
            return None
        versions, versions_truncated = self._fetchall_bounded(
            """SELECT * FROM reliability_agent_versions
            WHERE workspace_id=? AND agent_id=?
            ORDER BY created_at,agent_version_id""",
            (self.workspace_id, agent_id),
            max_items=_RUN_DETAIL_COLLECTION_LIMIT,
            byte_budget=[2 * 1024 * 1024],
        )
        agent["versions"] = versions
        agent["version_count"] = int(
            self.conn.execute(
                """SELECT COUNT(*) FROM reliability_agent_versions
                WHERE workspace_id=? AND agent_id=?""",
                (self.workspace_id, agent_id),
            ).fetchone()[0]
        )
        agent["returned_version_count"] = len(versions)
        agent["versions_truncated"] = versions_truncated
        return self._public_tree(agent)

    def list_scenario_suites(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        return self._public_tree(
            self._fetchall(
                """SELECT s.*,
                (SELECT COUNT(*) FROM reliability_scenarios c
                 WHERE c.workspace_id=s.workspace_id AND c.suite_id=s.suite_id)
                 AS scenario_count
            FROM reliability_scenario_suites s WHERE s.workspace_id=?
            ORDER BY s.created_at,s.suite_id LIMIT ? OFFSET ?""",
                (self.workspace_id, limit, offset),
            )
        )

    def get_scenario_suite(self, suite_id: str) -> dict[str, Any] | None:
        suite = self._fetchone(
            """SELECT s.*,
                (SELECT COUNT(*) FROM reliability_scenarios c
                 WHERE c.workspace_id=s.workspace_id AND c.suite_id=s.suite_id)
                 AS scenario_count
            FROM reliability_scenario_suites s
            WHERE s.workspace_id=? AND s.suite_id=?""",
            (self.workspace_id, suite_id),
        )
        if suite is None:
            return None
        suite["scenarios"] = self.list_scenarios(suite_id=suite_id, limit=200)
        return self._public_tree(suite)

    def list_scenarios(
        self,
        *,
        suite_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        sql = "SELECT * FROM reliability_scenarios WHERE workspace_id=?"
        params: list[Any] = [self.workspace_id]
        if suite_id is not None:
            sql += " AND suite_id=?"
            params.append(suite_id)
        sql += " ORDER BY created_at,scenario_id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return [self._scenario_public(row) for row in self._fetchall(sql, params)]

    def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM reliability_scenarios WHERE workspace_id=? AND scenario_id=?",
            (self.workspace_id, scenario_id),
        )
        return None if row is None else self._scenario_public(row)

    def list_campaigns(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        return self._public_tree(
            self._fetchall(
                """SELECT c.*,
                (SELECT COUNT(*) FROM reliability_conversation_runs r
                 WHERE r.workspace_id=c.workspace_id AND r.campaign_id=c.campaign_id)
                 AS run_count
            FROM reliability_campaigns c WHERE c.workspace_id=?
            ORDER BY c.created_at DESC,c.campaign_id LIMIT ? OFFSET ?""",
                (self.workspace_id, limit, offset),
            )
        )

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT c.*,
                (SELECT COUNT(*) FROM reliability_conversation_runs r
                 WHERE r.workspace_id=c.workspace_id AND r.campaign_id=c.campaign_id)
                 AS run_count
            FROM reliability_campaigns c
            WHERE c.workspace_id=? AND c.campaign_id=?""",
            (self.workspace_id, campaign_id),
        )
        return None if row is None else self._public_tree(row)

    def list_runs(
        self,
        *,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        sql = """SELECT r.*,
            (SELECT COUNT(*) FROM reliability_conversation_turns t
             WHERE t.workspace_id=r.workspace_id AND t.run_id=r.run_id) AS turn_count,
            (SELECT COUNT(*) FROM reliability_observed_tool_calls c
             WHERE c.workspace_id=r.workspace_id AND c.run_id=r.run_id) AS tool_call_count,
            (SELECT COUNT(*) FROM reliability_evaluation_results e
             WHERE e.workspace_id=r.workspace_id AND e.run_id=r.run_id) AS evaluation_count
            FROM reliability_conversation_runs r WHERE r.workspace_id=?"""
        params: list[Any] = [self.workspace_id]
        if campaign_id is not None:
            sql += " AND r.campaign_id=?"
            params.append(campaign_id)
        sql += " ORDER BY r.created_at,r.run_id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return self._public_tree(self._fetchall(sql, params))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._fetchone(
            """SELECT * FROM reliability_conversation_runs
            WHERE workspace_id=? AND run_id=?""",
            (self.workspace_id, run_id),
        )
        if run is None:
            return None
        truncated: list[str] = []
        byte_budget = [_MAX_RUN_DETAIL_BYTES]

        def read_rows(
            name: str,
            sql: str,
            params: Iterable[Any],
        ) -> list[dict[str, Any]]:
            rows, was_truncated = self._fetchall_bounded(
                sql,
                params,
                max_items=_RUN_DETAIL_COLLECTION_LIMIT,
                byte_budget=byte_budget,
            )
            if was_truncated:
                truncated.append(name)
            return rows

        turns = read_rows(
            "turns",
            """SELECT * FROM reliability_conversation_turns
            WHERE workspace_id=? AND run_id=? ORDER BY turn_index,turn_id""",
            (self.workspace_id, run_id),
        )
        calls = [
            self._tool_call_public(row)
            for row in read_rows(
                "tool_calls",
                """SELECT c.* FROM reliability_observed_tool_calls c
                JOIN reliability_conversation_turns t
                  ON t.workspace_id=c.workspace_id AND t.turn_id=c.turn_id
                WHERE c.workspace_id=? AND c.run_id=?
                ORDER BY t.turn_index,c.created_at,c.tool_call_id""",
                (self.workspace_id, run_id),
            )
        ]
        evaluations = [
            self._evaluation_public(row)
            for row in read_rows(
                "evaluations",
                """SELECT * FROM reliability_evaluation_results
                WHERE workspace_id=? AND run_id=?
                ORDER BY created_at,evaluation_id""",
                (self.workspace_id, run_id),
            )
        ]
        failures = [
            self._failure_public(row)
            for row in read_rows(
                "failures",
                """SELECT * FROM reliability_failures
                WHERE workspace_id=? AND run_id=?
                ORDER BY created_at,failure_id""",
                (self.workspace_id, run_id),
            )
        ]
        regressions = [
            self._regression_public(row)
            for row in read_rows(
                "regressions",
                """SELECT * FROM reliability_regressions
                WHERE workspace_id=? AND source_run_id=?
                ORDER BY created_at,regression_id""",
                (self.workspace_id, run_id),
            )
        ]
        manifests = [
            self._manifest_public(row)
            for row in read_rows(
                "manifests",
                """SELECT * FROM reliability_evidence_manifests
                WHERE workspace_id=? AND run_id=?
                ORDER BY created_at,manifest_id""",
                (self.workspace_id, run_id),
            )
        ]
        gates = [
            self._gate_public(row)
            for row in read_rows(
                "release_gates",
                """SELECT * FROM reliability_release_gates
                WHERE workspace_id=? AND campaign_id=?
                ORDER BY created_at,gate_id""",
                (self.workspace_id, run["campaign_id"]),
            )
        ]
        campaign = self.get_campaign(run["campaign_id"])
        detail = self._public_tree(
            {
                "run": run,
                "turns": turns,
                "tool_calls": calls,
                "evaluations": evaluations,
                "failures": failures,
                "regressions": regressions,
                "release_gates": gates,
                "manifests": manifests,
                "collection_limits": {
                    "max_items_per_collection": _RUN_DETAIL_COLLECTION_LIMIT,
                    "truncated": truncated,
                },
                "mis_links": {
                    "task_id": campaign.get("mis_task_id") if campaign else None,
                    "plan_id": campaign.get("mis_plan_id") if campaign else None,
                    "run_id": run.get("mis_run_id"),
                    "tool_call_ids": [
                        row.get("mis_tool_call_id")
                        for row in calls
                        if row.get("mis_tool_call_id")
                    ],
                    "evaluation_ids": [
                        row.get("mis_evaluation_id")
                        for row in evaluations
                        if row.get("mis_evaluation_id")
                    ],
                    "artifact_ids": [
                        row.get("mis_artifact_id")
                        for row in manifests
                        if row.get("mis_artifact_id")
                    ],
                    "approval_ids": [
                        row.get("mis_approval_id")
                        for row in gates
                        if row.get("mis_approval_id")
                    ],
                    "memory_ids": [
                        row.get("mis_memory_id")
                        for row in regressions
                        if row.get("mis_memory_id")
                    ],
                },
            }
        )
        encoded = json.dumps(
            detail,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_RUN_DETAIL_BYTES:
            raise RepositoryError(
                f"run detail exceeds {_MAX_RUN_DETAIL_BYTES} bytes; use bounded list endpoints"
            )
        return detail

    def list_failures(
        self,
        *,
        run_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        sql = "SELECT * FROM reliability_failures WHERE workspace_id=?"
        params: list[Any] = [self.workspace_id]
        if run_id is not None:
            sql += " AND run_id=?"
            params.append(run_id)
        sql += " ORDER BY created_at,failure_id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return [self._failure_public(row) for row in self._fetchall(sql, params)]

    def get_failure(self, failure_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            "SELECT * FROM reliability_failures WHERE workspace_id=? AND failure_id=?",
            (self.workspace_id, failure_id),
        )
        return None if row is None else self._failure_public(row)

    def list_failure_clusters(
        self,
        *,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        sql = """SELECT c.*,COUNT(m.failure_id) AS member_count
            FROM reliability_failure_clusters c
            LEFT JOIN reliability_failure_cluster_members m
              ON m.workspace_id=c.workspace_id AND m.cluster_id=c.cluster_id
            WHERE c.workspace_id=?"""
        params: list[Any] = [self.workspace_id]
        if campaign_id is not None:
            sql += " AND c.campaign_id=?"
            params.append(campaign_id)
        sql += """ GROUP BY c.workspace_id,c.cluster_id,c.schema_version,
            c.campaign_id,c.signature,c.created_at
            ORDER BY c.created_at,c.cluster_id LIMIT ? OFFSET ?"""
        params.extend((limit, offset))
        return self._public_tree(self._fetchall(sql, params))

    def get_failure_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        cluster = self._fetchone(
            """SELECT * FROM reliability_failure_clusters
            WHERE workspace_id=? AND cluster_id=?""",
            (self.workspace_id, cluster_id),
        )
        if cluster is None:
            return None
        members, members_truncated = self._fetchall_bounded(
            """SELECT m.failure_id,m.ordinal,f.*
            FROM reliability_failure_cluster_members m
            JOIN reliability_failures f
              ON f.workspace_id=m.workspace_id AND f.failure_id=m.failure_id
            WHERE m.workspace_id=? AND m.cluster_id=?
            ORDER BY m.ordinal""",
            (self.workspace_id, cluster_id),
            max_items=_RUN_DETAIL_COLLECTION_LIMIT,
            byte_budget=[2 * 1024 * 1024],
        )
        failures = [self._failure_public(row) for row in members]
        total_members = int(
            self.conn.execute(
                """SELECT COUNT(*) FROM reliability_failure_cluster_members
                WHERE workspace_id=? AND cluster_id=?""",
                (self.workspace_id, cluster_id),
            ).fetchone()[0]
        )
        result = dict(cluster)
        result["failure_case_ids"] = [row["failure_id"] for row in members]
        result["failures"] = failures
        result["member_count"] = total_members
        result["returned_member_count"] = len(members)
        result["members_truncated"] = members_truncated
        return self._public_tree(result)

    def list_regressions(
        self,
        *,
        scenario_id: str | None = None,
        source_run_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        sql = "SELECT * FROM reliability_regressions WHERE workspace_id=?"
        params: list[Any] = [self.workspace_id]
        if scenario_id is not None:
            sql += " AND scenario_id=?"
            params.append(scenario_id)
        if source_run_id is not None:
            sql += " AND source_run_id=?"
            params.append(source_run_id)
        sql += " ORDER BY created_at,regression_id LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return [self._regression_public(row) for row in self._fetchall(sql, params)]

    def get_regression(self, regression_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT * FROM reliability_regressions
            WHERE workspace_id=? AND regression_id=?""",
            (self.workspace_id, regression_id),
        )
        return None if row is None else self._regression_public(row)

    def list_release_gates(
        self,
        *,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        sql = "SELECT * FROM reliability_release_gates WHERE workspace_id=?"
        params: list[Any] = [self.workspace_id]
        if campaign_id is not None:
            sql += " AND campaign_id=?"
            params.append(campaign_id)
        sql += " ORDER BY created_at DESC,gate_id DESC LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        return [self._gate_public(row) for row in self._fetchall(sql, params)]

    def get_release_gate(self, gate_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT * FROM reliability_release_gates
            WHERE workspace_id=? AND gate_id=?""",
            (self.workspace_id, gate_id),
        )
        return None if row is None else self._gate_public(row)

    def list_evidence_manifests(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        offset = self._offset(offset)
        return [
            self._manifest_public(row)
            for row in self._fetchall(
                """SELECT * FROM reliability_evidence_manifests WHERE workspace_id=?
                ORDER BY created_at,manifest_id LIMIT ? OFFSET ?""",
                (self.workspace_id, limit, offset),
            )
        ]

    def get_evidence_manifest(self, manifest_id: str) -> dict[str, Any] | None:
        row = self._fetchone(
            """SELECT * FROM reliability_evidence_manifests
            WHERE workspace_id=? AND manifest_id=?""",
            (self.workspace_id, manifest_id),
        )
        return None if row is None else self._manifest_public(row)

    def _upsert_run(self, run: ConversationRun) -> str:
        data = run.model_dump(mode="json")
        return self._upsert(
            "reliability_conversation_runs",
            "run_id",
            {
                "workspace_id": self.workspace_id,
                "run_id": data["id"],
                "schema_version": data["schema_version"],
                "campaign_id": data["campaign_id"],
                "scenario_id": data["scenario_id"],
                "agent_version_id": data["agent_version_id"],
                "status": data["status"],
                "mis_run_id": data["mis_run_id"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "campaign_id",
                "scenario_id",
                "agent_version_id",
                "created_at",
            },
        )

    def _upsert_turn(self, turn: ConversationTurn) -> str:
        data = turn.model_dump(mode="json")
        return self._upsert(
            "reliability_conversation_turns",
            "turn_id",
            {
                "workspace_id": self.workspace_id,
                "turn_id": data["id"],
                "schema_version": data["schema_version"],
                "run_id": data["run_id"],
                "turn_index": data["turn_index"],
                "role": data["role"],
                "content": self._safe_text(data["content"], "turn.content"),
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "run_id",
                "turn_index",
                "created_at",
            },
        )

    def _upsert_tool_call(self, call: ObservedToolCall) -> str:
        data = call.model_dump(mode="json")
        return self._upsert(
            "reliability_observed_tool_calls",
            "tool_call_id",
            {
                "workspace_id": self.workspace_id,
                "tool_call_id": data["id"],
                "schema_version": data["schema_version"],
                "run_id": data["run_id"],
                "turn_id": data["turn_id"],
                "name": data["name"],
                "arguments_json": self._json(
                    self._safe_payload(data["arguments"], "tool_call.arguments"),
                    "arguments",
                ),
                "result_json": self._json(
                    self._safe_payload(data["result"], "tool_call.result"), "result"
                ),
                "error": (
                    None
                    if data["error"] is None
                    else self._safe_text(data["error"], "tool_call.error")
                ),
                "is_mutation": int(data["is_mutation"]),
                "duration_ms": data["duration_ms"],
                "mis_tool_call_id": data["mis_tool_call_id"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "run_id",
                "turn_id",
                "created_at",
            },
        )

    def _upsert_evaluation(self, evaluation: EvaluationResult) -> str:
        data = evaluation.model_dump(mode="json")
        return self._upsert(
            "reliability_evaluation_results",
            "evaluation_id",
            {
                "workspace_id": self.workspace_id,
                "evaluation_id": data["id"],
                "schema_version": data["schema_version"],
                "run_id": data["run_id"],
                "evaluator_id": data["evaluator_id"],
                "status": data["status"],
                "score": data["score"],
                "threshold": data["threshold"],
                "reason_codes_json": self._json(data["reason_codes"], "reason_codes"),
                "evidence_refs_json": self._json(
                    self._safe_payload(
                        data["evidence_refs"], "evaluation.evidence_refs"
                    ),
                    "evidence_refs",
                ),
                "metadata_json": self._json(
                    self._safe_payload(data["metadata"], "evaluation.metadata"),
                    "metadata",
                ),
                "mis_evaluation_id": data["mis_evaluation_id"],
                "created_at": data["created_at"],
            },
            immutable={
                "schema_version",
                "run_id",
                "evaluator_id",
                "created_at",
            },
        )

    def _authority(self, kind: str, value: str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        sql = _AUTHORITY_SQL[kind]
        try:
            row = self._fetchone(sql, (value,))
        except sqlite3.Error as exc:
            raise AuthorityMappingError(
                f"MIS {kind} authority is unavailable for mapping {value}"
            ) from exc
        if row is None:
            raise AuthorityMappingError(f"MIS {kind} mapping {value} does not exist")
        authority_workspace = row.get("authority_workspace_id")
        if authority_workspace != self.workspace_id:
            raise AuthorityMappingError(
                f"MIS {kind} mapping {value} belongs to workspace {authority_workspace!r}, "
                f"not {self.workspace_id!r}"
            )
        if kind == "artifact":
            workspaces = {
                item
                for item in (row.get("run_workspace_id"), row.get("task_workspace_id"))
                if item is not None
            }
            if len(workspaces) > 1:
                raise AuthorityMappingError(
                    f"MIS artifact mapping {value} has inconsistent workspace parents"
                )
        return row

    def _require_vertical(
        self,
        table: str,
        id_column: str,
        value: str,
    ) -> dict[str, Any]:
        row = self._fetchone(
            f"SELECT * FROM {table} WHERE workspace_id=? AND {id_column}=?",
            (self.workspace_id, value),
        )
        if row is None:
            raise RepositoryConflictError(
                f"missing vertical parent {table}.{id_column}={value} in workspace "
                f"{self.workspace_id}"
            )
        return row

    def _upsert(
        self,
        table: str,
        id_column: str,
        row: dict[str, Any],
        *,
        immutable: set[str],
        mutable: set[str] | frozenset[str] = frozenset(),
    ) -> str:
        existing = self._fetchone(
            f"SELECT * FROM {table} WHERE workspace_id=? AND {id_column}=?",
            (self.workspace_id, row[id_column]),
        )
        if existing is None:
            columns = list(row)
            try:
                self.conn.execute(
                    f"INSERT INTO {table}({','.join(columns)}) "
                    f"VALUES({','.join(':' + column for column in columns)})",
                    row,
                )
            except sqlite3.IntegrityError as exc:
                raise RepositoryConflictError(
                    f"{table}.{id_column}={row[id_column]} has a mapping or relational conflict"
                ) from exc
            return "created"

        immutable_columns = set(immutable)
        immutable_columns.update(
            column
            for column in row
            if column not in {"workspace_id", id_column}
            and not column.startswith("mis_")
            and column not in mutable
        )
        for column in sorted(immutable_columns):
            if existing[column] != row[column]:
                raise RepositoryConflictError(
                    f"{table}.{id_column}={row[id_column]} cannot rebind {column}"
                )
        for column in row:
            if not column.startswith("mis_"):
                continue
            if existing[column] is not None:
                if row[column] not in {None, existing[column]}:
                    raise RepositoryConflictError(
                        f"{table}.{id_column}={row[id_column]} cannot rebind {column}"
                    )
                row[column] = existing[column]

        mutable_columns = [
            column for column in row if column in mutable or column.startswith("mis_")
        ]
        if all(existing[column] == row[column] for column in mutable_columns):
            return "unchanged"
        assignments = ",".join(f"{column}=:{column}" for column in mutable_columns)
        try:
            self.conn.execute(
                f"UPDATE {table} SET {assignments} "
                f"WHERE workspace_id=:workspace_id AND {id_column}=:{id_column}",
                row,
            )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError(
                f"{table}.{id_column}={row[id_column]} has a mapping or relational conflict"
            ) from exc
        return "updated"

    @contextmanager
    def _savepoint(self) -> Iterator[None]:
        name = f"reliability_{next(self._savepoint_counter)}"
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.conn.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self.conn.execute(f"RELEASE SAVEPOINT {name}")

    def _fetchone(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> dict[str, Any] | None:
        cursor = self.conn.execute(sql, tuple(params))
        row = cursor.fetchone()
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return dict(zip((column[0] for column in cursor.description), row))

    def _fetchall(
        self,
        sql: str,
        params: Iterable[Any] = (),
    ) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, tuple(params))
        columns = [column[0] for column in cursor.description]
        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            result.append(
                dict(row) if isinstance(row, sqlite3.Row) else dict(zip(columns, row))
            )
        return result

    def _fetchall_bounded(
        self,
        sql: str,
        params: Iterable[Any] = (),
        *,
        max_items: int,
        byte_budget: list[int],
    ) -> tuple[list[dict[str, Any]], bool]:
        if max_items < 1 or len(byte_budget) != 1 or byte_budget[0] < 0:
            raise ValueError("bounded fetch requires a positive limit and byte budget")
        cursor = self.conn.execute(sql, tuple(params))
        columns = [column[0] for column in cursor.description]
        result: list[dict[str, Any]] = []
        while True:
            row = cursor.fetchone()
            if row is None:
                return result, False
            if len(result) >= max_items:
                return result, True
            item = (
                dict(row) if isinstance(row, sqlite3.Row) else dict(zip(columns, row))
            )
            item_size = len(
                json.dumps(
                    item,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if item_size > byte_budget[0]:
                return result, True
            result.append(item)
            byte_budget[0] -= item_size

    @staticmethod
    def _expect_model(value: object, model: type, path: str) -> None:
        if not isinstance(value, model):
            raise TypeError(f"{path} must be {model.__name__}")

    @staticmethod
    def _json(value: Any, path: str) -> str:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(f"{path} must be canonical JSON") from exc
        if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES:
            raise RepositoryError(f"{path} exceeds {_MAX_JSON_BYTES} bytes")
        return encoded

    @staticmethod
    def _decode(value: str, path: str) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"stored {path} is invalid JSON") from exc

    @classmethod
    def _public_tree(
        cls,
        value: Any,
        *,
        structural: bool = False,
        recognize_domain_rows: bool = True,
    ) -> Any:
        if isinstance(value, str):
            if structural:
                return value
            redacted = redact_full_text(value)
            return re.sub(
                r"(?i)\b(set-cookie|cookie)\s*[:=]\s*[^;\r\n]+",
                r"\1=[REDACTED]",
                redacted,
            )
        if isinstance(value, list):
            return [
                cls._public_tree(
                    item,
                    structural=structural,
                    recognize_domain_rows=recognize_domain_rows,
                )
                for item in value
            ]
        if isinstance(value, tuple):
            return [
                cls._public_tree(
                    item,
                    structural=structural,
                    recognize_domain_rows=recognize_domain_rows,
                )
                for item in value
            ]
        if isinstance(value, dict):
            safe: dict[str, Any] = {}
            role = str(value.get("role") or "").strip().lower()
            normalized_keys = {
                re.sub(r"[^a-z0-9]", "", str(key).lower()) for key in value
            }
            domain_row = (
                recognize_domain_rows
                and "workspaceid" in normalized_keys
                and bool(normalized_keys & _DOMAIN_ROW_ID_KEYS)
            )
            for key, child in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                sensitive = (
                    normalized in _SENSITIVE_PUBLIC_KEYS
                    or "authorization" in normalized
                    or normalized.endswith("credential")
                    or normalized.endswith("privatekey")
                    or normalized.endswith("apikey")
                    or normalized.endswith("password")
                    or normalized.endswith("secret")
                    or normalized.endswith("sessiontoken")
                    or normalized.endswith("accesstoken")
                    or normalized.endswith("refreshtoken")
                    or normalized.endswith("authtoken")
                    or normalized.endswith("sessionid")
                    or normalized.endswith("sessionids")
                    or normalized.endswith("tokenid")
                    or normalized.endswith("tokenids")
                    or normalized.endswith("credentialid")
                    or normalized.endswith("credentialids")
                    or normalized.endswith("credentialsid")
                    or normalized.endswith("credentialsids")
                    or normalized.endswith("apikeyid")
                    or normalized.endswith("apikeyids")
                    or "rawprompt" in normalized
                    or "rawmodelresponse" in normalized
                    or "hiddenprompt" in normalized
                    or "rawresponse" in normalized
                    or "systemprompt" in normalized
                )
                hidden_role_content = role in {
                    "system",
                    "developer",
                } and normalized in {
                    "content",
                    "message",
                    "prompt",
                    "text",
                }
                child_is_structural = (
                    structural
                    or (recognize_domain_rows and normalized == "mislinks")
                    or (domain_row and normalized in _STRUCTURAL_PUBLIC_KEYS)
                )
                safe[str(key)] = (
                    "[REDACTED]"
                    if sensitive or hidden_role_content
                    else cls._public_tree(
                        child,
                        structural=child_is_structural,
                        recognize_domain_rows=(
                            recognize_domain_rows
                            and normalized in _ROW_CONTAINER_PUBLIC_KEYS
                        ),
                    )
                )
            return safe
        return value

    @classmethod
    def _safe_payload(cls, value: Any, path: str) -> Any:
        def reject_oversized_text(node: Any, path: str) -> None:
            if isinstance(node, str):
                if len(node.encode("utf-8")) > _MAX_JSON_BYTES:
                    raise RepositoryError(f"{path} exceeds {_MAX_JSON_BYTES} bytes")
                return
            if isinstance(node, list):
                for index, child in enumerate(node):
                    reject_oversized_text(child, f"{path}[{index}]")
                return
            if isinstance(node, dict):
                for key, child in node.items():
                    reject_oversized_text(child, f"{path}.{key}")

        reject_oversized_text(value, path)
        return cls._public_tree(value, recognize_domain_rows=False)

    @classmethod
    def _safe_text(cls, value: str, path: str) -> str:
        safe = cls._safe_payload(value, path)
        if not isinstance(safe, str):
            raise RepositoryError(f"{path} must be text")
        return safe

    @staticmethod
    def _limit(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("limit must be an integer")
        return max(1, min(value, 200))

    @staticmethod
    def _offset(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("offset must be an integer")
        if not 0 <= value <= 1_000_000:
            raise ValueError("offset must be between 0 and 1000000")
        return value

    def _scenario_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        contract = {
            "schema_version": result["schema_version"],
            "id": result["scenario_id"],
            "name": result["name"],
            "persona": self._decode(result.pop("persona_json"), "persona_json"),
            "initial_message": result["initial_message"],
            "goal": self._decode(result.pop("goal_json"), "goal_json"),
            "challenges": self._decode(
                result.pop("challenges_json"), "challenges_json"
            ),
            "expectations": self._decode(
                result.pop("expectations_json"), "expectations_json"
            ),
            "tags": self._decode(result.pop("tags_json"), "tags_json"),
        }
        result["contract"] = contract
        return self._public_tree(result)

    def _tool_call_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["arguments"] = self._decode(
            result.pop("arguments_json"), "arguments_json"
        )
        result["result"] = self._decode(result.pop("result_json"), "result_json")
        result["is_mutation"] = bool(result["is_mutation"])
        return self._public_tree(result)

    def _evaluation_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for public, stored in (
            ("reason_codes", "reason_codes_json"),
            ("evidence_refs", "evidence_refs_json"),
            ("metadata", "metadata_json"),
        ):
            result[public] = self._decode(result.pop(stored), stored)
        return self._public_tree(result)

    def _failure_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for public, stored in (
            ("expected", "expected_json"),
            ("observed", "observed_json"),
            ("evidence_refs", "evidence_refs_json"),
        ):
            result[public] = self._decode(result.pop(stored), stored)
        return self._public_tree(result)

    def _regression_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for public, stored in (
            ("original_input", "original_input_json"),
            ("expected", "expected_json"),
            ("observed", "observed_json"),
            ("evidence_refs", "evidence_refs_json"),
        ):
            result[public] = self._decode(result.pop(stored), stored)
        return self._public_tree(result)

    def _gate_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for public, stored in (
            ("blockers", "blockers_json"),
            ("warnings", "warnings_json"),
            ("metrics", "metrics_json"),
            ("evidence_refs", "evidence_refs_json"),
        ):
            result[public] = self._decode(result.pop(stored), stored)
        return self._public_tree(result)

    def _manifest_public(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for public, stored in (
            ("environment", "environment_json"),
            ("evaluator_versions", "evaluator_versions_json"),
            ("artifacts", "artifacts_json"),
        ):
            result[public] = self._decode(result.pop(stored), stored)
        return self._public_tree(result)


__all__ = ["RELIABILITY_SCHEMA_SQL", "SQLiteRepository"]
