from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from open_cekura.campaigns.service import compare_campaigns, run_campaign
from open_cekura.evidence.manifest import (
    canonical_json_bytes,
    sha256_bytes,
    verified_campaign_json,
    verify_campaign,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_SUITE = REPO_ROOT / "examples" / "open-cekura" / "scenarios"
CAMPAIGN_ID = "occampaign_evidence_integrity"
BASELINE_ID = "occampaign_evidence_baseline"
COMPARED_CANDIDATE_ID = "occampaign_evidence_compared_candidate"


@pytest.fixture(scope="module")
def governed_campaign(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("campaign-integrity")
    artifacts = root / "artifacts"
    run_campaign(
        suite_path=SCENARIO_SUITE,
        agent="mock",
        version="candidate",
        campaign_id=CAMPAIGN_ID,
        workspace_id="default",
        db_path=root / "mis.db",
        artifact_root=artifacts,
    )
    assert verify_campaign(artifacts, CAMPAIGN_ID).ok is True
    return artifacts


@pytest.fixture(scope="module")
def compared_campaigns(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("compared-campaign-integrity")
    artifacts = root / "artifacts"
    database = root / "mis.db"
    for campaign_id, version in (
        (BASELINE_ID, "baseline"),
        (COMPARED_CANDIDATE_ID, "candidate"),
    ):
        run_campaign(
            suite_path=SCENARIO_SUITE,
            agent="mock",
            version=version,
            campaign_id=campaign_id,
            workspace_id="default",
            db_path=database,
            artifact_root=artifacts,
        )
    compare_campaigns(
        baseline_campaign_id=BASELINE_ID,
        candidate_campaign_id=COMPARED_CANDIDATE_ID,
        workspace_id="default",
        db_path=database,
        artifact_root=artifacts,
    )
    assert verify_campaign(artifacts, BASELINE_ID).ok is True
    assert verify_campaign(artifacts, COMPARED_CANDIDATE_ID).ok is True
    return artifacts


def _mutate_failure_count(summary: dict[str, object]) -> None:
    summary["failure_count"] = int(summary["failure_count"]) + 1


def _mutate_gate_metric(summary: dict[str, object]) -> None:
    gate_input = summary["gate_input"]
    assert isinstance(gate_input, dict)
    metrics = gate_input["metrics"]
    assert isinstance(metrics, dict)
    metrics["task_success_pass_count"] = int(metrics["task_success_pass_count"]) - 1


def _mutate_gate_result_group(summary: dict[str, object]) -> None:
    gate_input = summary["gate_input"]
    assert isinstance(gate_input, dict)
    metrics = gate_input["metrics"]
    assert isinstance(metrics, dict)
    groups = metrics["result_groups"]
    assert isinstance(groups, list)
    group = groups[0]
    assert isinstance(group, dict)
    results = group["deterministic_results"]
    assert isinstance(results, list)
    result = results[0]
    assert isinstance(result, dict)
    result["mis_evaluation_id"] = "evl_forged_mapping"


def _mutate_run_ids(summary: dict[str, object]) -> None:
    run_ids = summary["run_ids"]
    assert isinstance(run_ids, list)
    run_ids.pop()


def _mutate_manifest_ids(summary: dict[str, object]) -> None:
    manifest_ids = summary["manifest_ids"]
    assert isinstance(manifest_ids, list)
    manifest_ids[0] = "ocmanifest_forged"


def _mutate_scenario_ids(summary: dict[str, object]) -> None:
    scenario_ids = summary["scenario_ids"]
    assert isinstance(scenario_ids, list)
    scenario_ids[0] = "scenario.forged"


def _mutate_git_commit(summary: dict[str, object]) -> None:
    summary["git_commit_sha"] = "f" * 40


def _mutate_mis_task_mapping(summary: dict[str, object]) -> None:
    summary["mis_task_id"] = "tsk_forged"


def _mutate_plan_evidence_mapping(summary: dict[str, object]) -> None:
    plan_evidence = summary["plan_evidence"]
    assert isinstance(plan_evidence, list)
    row = plan_evidence[0]
    assert isinstance(row, dict)
    row["status"] = "forged"


def _mutate_comparison(summary: dict[str, object]) -> None:
    summary["comparison"] = {
        "schema_version": 1,
        "baseline_campaign_id": "occampaign_forged",
        "candidate_campaign_id": CAMPAIGN_ID,
    }


def _mutate_agent_name(summary: dict[str, object]) -> None:
    agent = summary["agent"]
    assert isinstance(agent, dict)
    agent["name"] = "Forged Appointment Agent"


@pytest.mark.parametrize(
    "mutation",
    [
        _mutate_failure_count,
        _mutate_gate_metric,
        _mutate_gate_result_group,
        _mutate_run_ids,
        _mutate_manifest_ids,
        _mutate_scenario_ids,
        _mutate_git_commit,
        _mutate_mis_task_mapping,
        _mutate_plan_evidence_mapping,
        _mutate_comparison,
        _mutate_agent_name,
    ],
    ids=[
        "failure-count",
        "gate-metrics",
        "gate-result-groups",
        "run-ids",
        "manifest-ids",
        "scenario-ids",
        "git-commit",
        "mis-task-mapping",
        "plan-evidence-mapping",
        "comparison",
        "agent-name",
    ],
)
def test_campaign_verification_rejects_forged_summary_facts(
    governed_campaign: Path,
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(governed_campaign, artifacts)
    summary_path = artifacts / CAMPAIGN_ID / "campaign_summary.json"
    envelope = json.loads(summary_path.read_bytes())
    summary = envelope["summary"]
    assert isinstance(summary, dict)
    mutation(summary)
    summary_path.write_bytes(canonical_json_bytes(envelope))

    report = verify_campaign(artifacts, CAMPAIGN_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


def test_verified_campaign_snapshot_cannot_be_replaced_after_verification(
    governed_campaign: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(governed_campaign, artifacts)
    report = verify_campaign(artifacts, CAMPAIGN_ID)
    original = verified_campaign_json(report, "campaign_summary.json")
    assert isinstance(original, dict)
    original_failure_count = original["summary"]["failure_count"]

    summary_path = artifacts / CAMPAIGN_ID / "campaign_summary.json"
    forged = json.loads(summary_path.read_bytes())
    forged["summary"]["failure_count"] = original_failure_count + 1
    summary_path.write_bytes(canonical_json_bytes(forged))

    hydrated = verified_campaign_json(report, "campaign_summary.json")
    assert hydrated["summary"]["failure_count"] == original_failure_count
    assert verify_campaign(artifacts, CAMPAIGN_ID).ok is False


def test_governed_run_mappings_cannot_be_downgraded_to_arbitrary_summary(
    governed_campaign: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(governed_campaign, artifacts)
    summary_path = artifacts / CAMPAIGN_ID / "campaign_summary.json"
    original = json.loads(summary_path.read_bytes())
    downgraded = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "summary": {"run_count": 10, "pass_rate": 1.0},
        "artifacts": original["artifacts"],
    }
    summary_path.write_bytes(canonical_json_bytes(downgraded))

    report = verify_campaign(artifacts, CAMPAIGN_ID)

    assert report.ok is False
    assert "governed_campaign_summary_required" in {
        issue.code for issue in report.issues
    }


def test_compared_campaign_requires_existing_verified_baseline(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    shutil.rmtree(artifacts / BASELINE_ID)

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_baseline_unverified" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("mutation", ["metrics", "run_ids", "scenario_ids"])
def test_compared_campaign_rejects_tampered_baseline_facts(
    compared_campaigns: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    summary_path = artifacts / BASELINE_ID / "campaign_summary.json"
    baseline = json.loads(summary_path.read_bytes())
    summary = baseline["summary"]
    if mutation == "metrics":
        summary["gate_input"]["metrics"]["task_success_pass_count"] -= 1
    elif mutation == "run_ids":
        summary["run_ids"].pop()
    else:
        summary["scenario_ids"][0] = "scenario.forged"
    summary_path.write_bytes(canonical_json_bytes(baseline))

    assert verify_campaign(artifacts, BASELINE_ID).ok is False
    candidate = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)
    assert candidate.ok is False
    assert "campaign_baseline_unverified" in {
        issue.code for issue in candidate.issues
    }


@pytest.mark.parametrize(
    "field",
    ["name", "original_input", "expected", "observed", "evidence_refs", "created_at"],
)
def test_regression_cases_are_fully_anchored_to_verified_run_facts(
    compared_campaigns: Path,
    tmp_path: Path,
    field: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / BASELINE_ID
    regressions_path = campaign_path / "regression_cases.json"
    regressions = json.loads(regressions_path.read_bytes())
    assert regressions
    regression = regressions[0]
    if field == "name":
        regression[field] = "Forged regression name"
    elif field == "original_input":
        regression[field]["initial_message"] = "Forged input"
    elif field == "expected":
        regression[field] = {"evaluation_status": "pass", "final_state": {}}
    elif field == "observed":
        regression[field] = {"evaluation_status": "fail", "final_state": {}}
    elif field == "evidence_refs":
        regression[field].append("artifact:transcript.json")
    else:
        regression[field] = "2026-08-11T00:00:00Z"
    changed = canonical_json_bytes(regressions)
    regressions_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["artifacts"]["regression_cases.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, BASELINE_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


@pytest.mark.parametrize(
    "field",
    ["candidate_campaign_id", "candidate_metrics", "delta"],
)
def test_comparison_diff_is_reconstructed_in_full(
    compared_campaigns: Path,
    tmp_path: Path,
    field: str,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    diff_path = campaign_path / "baseline_candidate_diff.json"
    diff = json.loads(diff_path.read_bytes())
    if field == "candidate_campaign_id":
        diff[field] = "occampaign_forged"
    elif field == "candidate_metrics":
        diff[field]["task_success_pass_count"] -= 1
    else:
        diff[field]["task_success_percentage_points"] = 99.0
    changed = canonical_json_bytes(diff)
    diff_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["summary"]["comparison"] = diff
    summary["artifacts"]["baseline_candidate_diff.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_summary_facts_mismatch" in {
        issue.code for issue in report.issues
    }


def test_path_unsafe_comparison_baseline_is_a_safe_verification_failure(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    diff_path = campaign_path / "baseline_candidate_diff.json"
    diff = json.loads(diff_path.read_bytes())
    diff["baseline_campaign_id"] = "C:evil"
    changed = canonical_json_bytes(diff)
    diff_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["summary"]["comparison"] = diff
    summary["artifacts"]["baseline_candidate_diff.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_baseline_unverified" in {
        issue.code for issue in report.issues
    }


def test_comparison_cycle_fails_without_recursive_verification(
    compared_campaigns: Path,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    shutil.copytree(compared_campaigns, artifacts)
    campaign_path = artifacts / COMPARED_CANDIDATE_ID
    diff_path = campaign_path / "baseline_candidate_diff.json"
    diff = json.loads(diff_path.read_bytes())
    diff["baseline_campaign_id"] = COMPARED_CANDIDATE_ID
    changed = canonical_json_bytes(diff)
    diff_path.write_bytes(changed)
    summary_path = campaign_path / "campaign_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["summary"]["comparison"] = diff
    summary["artifacts"]["baseline_candidate_diff.json"] = sha256_bytes(changed)
    summary_path.write_bytes(canonical_json_bytes(summary))

    report = verify_campaign(artifacts, COMPARED_CANDIDATE_ID)

    assert report.ok is False
    assert "campaign_baseline_unverified" in {
        issue.code for issue in report.issues
    }
