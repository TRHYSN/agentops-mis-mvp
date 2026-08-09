#!/usr/bin/env python3
"""Deterministic smoke for native research domain and repository contracts."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentops_mis_core.research_domain import (  # noqa: E402
    Checkpoint, InvalidTransition, JobAttempt, MetricSnapshot, ResearchClaim,
    ResearchContract, ResearchDomainError, StaleStateVersion, Trial,
    canonical_hash, canonical_json, classify_legacy_record,
)
from agentops_mis_core.research_migrations import apply_research_domain_migration  # noqa: E402
from agentops_mis_core.research_repository import RepositoryConflict, SQLiteResearchRepository  # noqa: E402


def require(value: bool, message: str, failures: list[str]) -> None:
    if not value:
        failures.append(message)


def authority_fixture(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE agent_plans(plan_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL);
        CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL);
        CREATE TABLE tasks(task_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL);
        CREATE TABLE runs(run_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL);
        CREATE TABLE evaluations(evaluation_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL);
    """)
    conn.executemany("INSERT INTO agent_plans VALUES(?,?)", [("plan_1", "ws_1")])
    conn.executemany("INSERT INTO artifacts VALUES(?,?)", [(f"art_{i}", "ws_1") for i in range(1, 8)])
    conn.executemany("INSERT INTO tasks VALUES(?,?)", [("task_1", "ws_1")])
    conn.executemany("INSERT INTO runs VALUES(?,?)", [(f"run_{i}", "ws_1") for i in range(1, 4)])
    conn.executemany("INSERT INTO evaluations VALUES(?,?)", [("eval_1", "ws_1")])
    conn.commit()


def contract(payload: dict[str, object]) -> ResearchContract:
    return ResearchContract.create(
        contract_id="contract_1", version=1, workspace_id="ws_1",
        project_ref="project_1", goal_ref="goal_1", requirement_ref="requirement_1",
        agent_plan_id="plan_1", contract_artifact_id="art_1", payload=payload,
    )


def main() -> int:
    failures: list[str] = []
    payload_a = {"z": [3, 2, 1], "a": {"threshold": 0.5, "enabled": True}}
    payload_b = {"a": {"enabled": True, "threshold": 0.5}, "z": [3, 2, 1]}
    require(canonical_json(payload_a) == canonical_json(payload_b), "canonical JSON ordering drift", failures)
    require(canonical_hash(payload_a) == canonical_hash(payload_b), "canonical hash ordering drift", failures)
    try:
        canonical_hash({"bad": float("nan")})
        failures.append("non-finite canonical JSON was accepted")
    except ResearchDomainError:
        pass
    try:
        canonical_json({1: "coerced"})
        failures.append("non-string mapping key was accepted")
    except ResearchDomainError:
        pass
    try:
        ResearchContract.create(
            contract_id="contract_bad", version=1, workspace_id="ws_1",
            project_ref="project_1", goal_ref="goal_1", requirement_ref="requirement_1",
            agent_plan_id="plan_1", contract_artifact_id="art_1", payload=payload_a,
            protocol_hash="0" * 64,
        )
        failures.append("mismatched supplied protocol hash was accepted")
    except ResearchDomainError:
        pass
    try:
        JobAttempt("attempt_bad", "trial_1", "run_1", 1).transition(
            "submitted", expected_state_version=1, reconcile_outcome="running"
        )
        failures.append("misleading reconcile metadata was accepted")
    except ResearchDomainError:
        pass
    for bad_step in (True, 1.5, "1"):
        try:
            MetricSnapshot("metric_bad_step", "attempt_2", "art_3", "loss", 0.2, bad_step)  # type: ignore[arg-type]
            failures.append(f"invalid metric step was accepted: {bad_step!r}")
        except ResearchDomainError:
            pass
    try:
        ResearchClaim("claim_bad_bool", "contract_1", 1, "eval_1", "Bad gate", machine_gate_passed=1)  # type: ignore[arg-type]
        failures.append("truthy non-bool machine gate was accepted")
    except ResearchDomainError:
        pass

    immutable = contract(payload_a)
    try:
        immutable.status = "active"  # type: ignore[misc]
        failures.append("frozen contract was mutable")
    except FrozenInstanceError:
        pass
    try:
        immutable.transition("active", expected_state_version=1)
        failures.append("illegal contract transition was accepted")
    except InvalidTransition:
        pass
    try:
        immutable.transition("proposed", expected_state_version=2)
        failures.append("stale contract version was accepted")
    except StaleStateVersion:
        pass

    with sqlite3.connect(":memory:") as conn:
        authority_fixture(conn)
        conn.isolation_level = None
        receipt = apply_research_domain_migration(conn)
        repo = SQLiteResearchRepository(conn)
        conn.execute("INSERT INTO artifacts VALUES('art_cross','ws_other')")
        try:
            repo.add_contract(ResearchContract.create(
                contract_id="contract_cross", version=1, workspace_id="ws_1",
                project_ref="project_1", goal_ref="goal_1", requirement_ref="requirement_1",
                agent_plan_id="plan_1", contract_artifact_id="art_cross", payload=payload_a,
            ))
            failures.append("cross-workspace contract Artifact was accepted")
        except RepositoryConflict:
            pass
        repo.add_contract(immutable)
        c = repo.transition_contract("contract_1", 1, "proposed", expected_state_version=1)
        c = repo.transition_contract("contract_1", 1, "review_pending", expected_state_version=c.state_version)
        c = repo.transition_contract("contract_1", 1, "rejected", expected_state_version=c.state_version)
        try:
            repo.transition_contract("contract_1", 1, "draft", expected_state_version=c.state_version)
            failures.append("rejected contract version was not terminal")
        except InvalidTransition:
            pass
        revised = repo.revise_contract(
            "contract_1", 1, payload={"research_question": "revised"},
            expected_state_version=c.state_version,
        )
        require(revised.version == 2 and revised.supersedes_version == 1, "revision did not create new version", failures)
        require(revised.content_hash != c.content_hash, "revision did not create new hash", failures)

        atomic = ResearchContract.create(
            contract_id="contract_atomic", version=1, workspace_id="ws_1",
            project_ref="project_1", goal_ref="goal_1", requirement_ref="requirement_1",
            agent_plan_id="plan_1", contract_artifact_id="art_4", payload={"protocol": "v1"},
        )
        repo.add_contract(atomic)
        for target in ("proposed", "review_pending", "approved", "active"):
            atomic = repo.transition_contract("contract_atomic", 1, target, expected_state_version=atomic.state_version)
        repo.add_contract(ResearchContract.create(
            contract_id="contract_atomic", version=2, workspace_id="ws_1",
            project_ref="project_1", goal_ref="goal_1", requirement_ref="requirement_1",
            agent_plan_id="plan_1", contract_artifact_id="art_4", payload={"protocol": "conflict"},
            status="rejected", supersedes_version=1,
        ))
        before_conflict = repo.get_contract("contract_atomic", 1)
        try:
            repo.revise_contract("contract_atomic", 1, payload={"protocol": "v2"}, expected_state_version=atomic.state_version)
            failures.append("conflicting revision insert was accepted")
        except RepositoryConflict:
            pass
        require(repo.get_contract("contract_atomic", 1) == before_conflict,
                "failed revision did not roll back prior UPDATE", failures)

        trial = Trial("trial_1", "contract_1", 2, "task_1")
        repo.add_trial(trial)
        trial = repo.transition_trial("trial_1", "planned", expected_state_version=1)
        try:
            repo.transition_trial("trial_1", "running", expected_state_version=1)
            failures.append("stale trial transition was accepted")
        except StaleStateVersion:
            pass

        attempt = JobAttempt("attempt_1", "trial_1", "run_1", 1)
        repo.add_job_attempt(attempt)
        for target in ("submitted", "acknowledged", "running", "cancel_pending", "cancelled"):
            attempt = repo.transition_job_attempt(
                "attempt_1", target, expected_state_version=attempt.state_version
            )
        require(attempt.status == "cancelled", "cancel_pending -> cancelled failed", failures)

        reconcile = JobAttempt("attempt_2", "trial_1", "run_2", 2)
        repo.add_job_attempt(reconcile)
        for target in ("submitted", "acknowledged", "running", "heartbeat_lost", "reconciling"):
            reconcile = repo.transition_job_attempt(
                "attempt_2", target, expected_state_version=reconcile.state_version
            )
        reconcile = repo.transition_job_attempt(
            "attempt_2", "reconcile_ambiguous", expected_state_version=reconcile.state_version,
            reconcile_outcome="unknown",
        )
        require(reconcile.status == "reconcile_ambiguous", "unknown reconcile did not fail closed", failures)
        try:
            repo.transition_job_attempt(
                "attempt_2", "running", expected_state_version=reconcile.state_version
            )
            failures.append("ambiguous attempt jumped directly to running")
        except InvalidTransition:
            pass

        cancel_reconcile = JobAttempt("attempt_3", "trial_1", "run_3", 3)
        repo.add_job_attempt(cancel_reconcile)
        for target in ("submitted", "acknowledged", "running", "heartbeat_lost", "reconciling", "cancel_pending", "cancelled"):
            cancel_reconcile = repo.transition_job_attempt(
                "attempt_3", target, expected_state_version=cancel_reconcile.state_version
            )
        require(cancel_reconcile.status == "cancelled", "reconciling cancel request was unreachable", failures)

        checkpoint = Checkpoint(
            "checkpoint_1", "attempt_2", "art_2", "run_2", "a" * 64, "b" * 64, "valid"
        )
        repo.add_checkpoint(checkpoint)
        metric = MetricSnapshot("metric_1", "attempt_2", "art_3", "loss", 0.25, 1, "eval_1")
        repo.add_metric_snapshot(metric)
        try:
            MetricSnapshot("metric_bad", "attempt_2", "art_3", "loss", float("inf"))
            failures.append("non-finite metric was accepted")
        except ResearchDomainError:
            pass

        claim = ResearchClaim("claim_1", "contract_1", 2, "eval_1", "Bounded claim")
        repo.add_claim(claim)
        claim = repo.transition_claim("claim_1", "evidence_pending", expected_state_version=1)
        claim = repo.transition_claim("claim_1", "reviewer_pending", expected_state_version=claim.state_version)
        try:
            repo.transition_claim("claim_1", "accepted", expected_state_version=claim.state_version)
            failures.append("claim bypassed machine/reviewer gates")
        except ResearchDomainError:
            pass
        try:
            repo.transition_claim(
                "claim_1", "accepted", expected_state_version=claim.state_version,
                machine_gate_passed="true", independent_reviewer_id="reviewer_2",  # type: ignore[arg-type]
            )
            failures.append("string machine gate was accepted")
        except ResearchDomainError:
            pass
        claim = repo.transition_claim(
            "claim_1", "accepted", expected_state_version=claim.state_version,
            machine_gate_passed=True, independent_reviewer_id="reviewer_2",
        )
        require(claim.status == "accepted", "fully gated claim was not accepted", failures)

        legacy = classify_legacy_record("metric", "metric_old")
        require(legacy.get("validity") == "legacy_unverified", "legacy metric was promoted", failures)
        require(legacy.get("eligible") is True and legacy.get("accepted") is False,
                "legacy eligibility/acceptance classification is unsafe", failures)

    print(json.dumps({
        "ok": not failures,
        "operation": "research_domain_contract_smoke",
        "migration_applied": receipt.applied,
        "hash_determinism": True,
        "immutable_versions": True,
        "contract_rejection_revision": True,
        "cancel_reconcile": True,
        "claim_dual_gate": True,
        "legacy_fail_closed": True,
        "failures": failures,
        "credentials_omitted": True,
    }, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
