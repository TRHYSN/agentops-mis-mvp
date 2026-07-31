from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .executors import ExecutionRequest, LocalExecutor
from .integrity import compare_actuals, evaluate_claim_eligibility
from .ledger import ResearchLedger
from .protocol import ExperimentSpec, sha256_json


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    metrics: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid metric JSON at line {line_number}") from exc
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError(f"invalid metric record at line {line_number}")
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"metric value must be numeric at line {line_number}")
        step = item.get("step")
        if step is not None and (isinstance(step, bool) or not isinstance(step, int)):
            raise ValueError(f"metric step must be an integer at line {line_number}")
        metrics.append(
            {
                "name": item["name"],
                "value": float(value),
                "step": step,
                "recorded_at": item.get("recorded_at"),
            }
        )
    return metrics


def _collect_artifacts(path: Path, attempt_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"artifact symlinks are not allowed: {candidate.name}")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(path).as_posix()
        digest = _sha256_file(candidate)
        if digest is None:
            continue
        artifacts.append(
            {
                "id": f"art_{sha256_json({'attempt_id': attempt_id, 'path': relative, 'sha256': digest})[:16]}",
                "relative_path": relative,
                "sha256": digest,
                "size_bytes": candidate.stat().st_size,
            }
        )
    return artifacts


def _runtime_environment(
    *,
    spec: ExperimentSpec,
    experiment_id: str,
    trial_id: str,
    attempt_id: str,
    params: dict[str, Any],
    run_dir: Path,
) -> dict[str, str]:
    allowed_parent = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
    env = {key: os.environ[key] for key in allowed_parent if key in os.environ}
    env.update(spec.environment or {})
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONUNBUFFERED"] = "1"
    env["RESEARCH_LAB_METRICS_PATH"] = str(run_dir / "metrics.jsonl")
    env["RESEARCH_LAB_ACTUALS_PATH"] = str(run_dir / "actuals.json")
    env["RESEARCH_LAB_ARTIFACTS_DIR"] = str(run_dir / "artifacts")
    env["RESEARCH_LAB_PROTOCOL_HASH"] = spec.protocol_hash
    env["RESEARCH_LAB_PROVENANCE_HASH"] = spec.provenance_hash or ""
    env["RESEARCH_LAB_RESOLVED_CONFIG_HASH"] = (
        spec.provenance.resolved_config_hash if spec.provenance else ""
    ) or ""
    env["RESEARCH_LAB_CODE_REVISION"] = (
        spec.provenance.code.revision if spec.provenance and spec.provenance.code else ""
    )
    env["RESEARCH_LAB_EXPERIMENT_ID"] = experiment_id
    env["RESEARCH_LAB_TRIAL_ID"] = trial_id
    env["RESEARCH_LAB_ATTEMPT_ID"] = attempt_id
    for key, value in params.items():
        normalized = "".join(ch if ch.isalnum() else "_" for ch in key.upper())
        env[f"RESEARCH_LAB_PARAM_{normalized}"] = str(value)
    return env


class LocalExperimentRunner:
    def __init__(self, *, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = ResearchLedger(self.state_dir / "research-lab.db")

    async def run(self, *, spec: ExperimentSpec, spec_path: Path) -> dict[str, Any]:
        if spec.executor != "local":
            raise ValueError("run-local only accepts executor='local'")
        workdir = (spec_path.resolve().parent / spec.workdir).resolve()
        if not workdir.is_dir():
            raise ValueError(f"experiment workdir does not exist: {workdir}")

        experiment_id = f"exp_{spec.protocol_hash[:16]}"
        protocol_document = dict(spec.protocol_document)
        protocol_document["protocol_hash"] = spec.protocol_hash
        self.ledger.upsert_experiment(
            experiment_id=experiment_id,
            name=spec.name,
            stage=spec.stage.value,
            protocol_hash=spec.protocol_hash,
            provenance_hash=spec.provenance_hash,
            protocol_document=protocol_document,
        )
        trial_inputs: list[tuple[str, dict[str, Any]]] = []
        for params in spec.expand_trials():
            trial_id = f"trl_{sha256_json({'experiment_id': experiment_id, 'params': params})[:16]}"
            self.ledger.ensure_trial(trial_id=trial_id, experiment_id=experiment_id, params=params)
            trial_inputs.append((trial_id, params))

        self.ledger.set_experiment_status(experiment_id, "running")
        semaphore = asyncio.Semaphore(spec.max_concurrency)

        async def run_trial(trial_id: str, params: dict[str, Any]) -> None:
            if self.ledger.trial_status(trial_id) in {"completed", "completed_with_deviation"}:
                return
            async with semaphore:
                self.ledger.set_trial_status(trial_id, "running")
                terminal_status = "failed"
                first_attempt = self.ledger.next_attempt_number(trial_id)
                for attempt_number in range(first_attempt, first_attempt + spec.retries + 1):
                    attempt_id = f"att_{sha256_json({'trial_id': trial_id, 'attempt': attempt_number})[:16]}"
                    run_dir = self.state_dir / "runs" / experiment_id / trial_id / attempt_id
                    run_dir.mkdir(parents=True, exist_ok=False)
                    stdout_path = run_dir / "stdout.log"
                    stderr_path = run_dir / "stderr.log"
                    self.ledger.start_attempt(
                        attempt_id=attempt_id,
                        trial_id=trial_id,
                        attempt_number=attempt_number,
                        run_dir=run_dir,
                    )
                    request = ExecutionRequest(
                        command=spec.render_command(params),
                        cwd=workdir,
                        env=_runtime_environment(
                            spec=spec,
                            experiment_id=experiment_id,
                            trial_id=trial_id,
                            attempt_id=attempt_id,
                            params=params,
                            run_dir=run_dir,
                        ),
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        timeout_seconds=spec.timeout_seconds,
                    )
                    result = await LocalExecutor().run(request)
                    evidence_error: str | None = None
                    metrics: list[dict[str, Any]] = []
                    artifacts: list[dict[str, Any]] = []
                    actuals = _load_json(run_dir / "actuals.json")
                    try:
                        metrics = _load_metrics(run_dir / "metrics.jsonl")
                        artifacts = _collect_artifacts(run_dir / "artifacts", attempt_id)
                    except ValueError as exc:
                        evidence_error = str(exc)
                    deviations = [
                        {
                            "field_path": item.field_path,
                            "expected": item.expected,
                            "actual": item.actual,
                            "severity": item.severity,
                            "message": item.message,
                        }
                        for item in compare_actuals(protocol_document, actuals, spec.integrity)
                    ]
                    if evidence_error:
                        deviations.append(
                            {
                                "field_path": "runtime.evidence",
                                "expected": "valid",
                                "actual": "invalid",
                                "severity": "critical",
                                "message": evidence_error,
                            }
                        )
                    self.ledger.replace_attempt_evidence(
                        trial_id=trial_id,
                        attempt_id=attempt_id,
                        metrics=metrics,
                        artifacts=artifacts,
                        deviations=deviations,
                    )
                    attempt_status = result.status
                    if result.status == "completed" and (
                        evidence_error or any(item["severity"] == "critical" for item in deviations)
                    ):
                        attempt_status = "completed_with_deviation"
                    self.ledger.finish_attempt(
                        attempt_id=attempt_id,
                        status=attempt_status,
                        exit_code=result.exit_code,
                        error_summary=evidence_error or result.error_summary,
                        stdout_sha256=_sha256_file(stdout_path),
                        stderr_sha256=_sha256_file(stderr_path),
                    )
                    if attempt_status == "completed":
                        self.ledger.supersede_prior_attempt_deviations(
                            trial_id=trial_id,
                            current_attempt_id=attempt_id,
                        )
                    terminal_status = attempt_status
                    if result.status == "completed":
                        break
                self.ledger.set_trial_status(trial_id, terminal_status)

        await asyncio.gather(*(run_trial(trial_id, params) for trial_id, params in trial_inputs))
        evidence = self.ledger.experiment_evidence(experiment_id)
        metric_names: dict[str, set[str]] = {}
        final_metric_values: dict[str, dict[str, float]] = {}
        for metric in evidence["metrics"]:
            trial_id = str(metric["trial_id"])
            metric_name = str(metric["name"])
            metric_names.setdefault(trial_id, set()).add(metric_name)
            final_metric_values.setdefault(trial_id, {})[metric_name] = float(metric["value"])
        claim = evaluate_claim_eligibility(
            evidence["experiment"],
            evidence["trials"],
            evidence["deviations"],
            metric_names,
            final_metric_values,
        )
        trial_statuses = {str(item["status"]) for item in evidence["trials"]}
        if trial_statuses <= {"completed"}:
            status = "completed"
        elif trial_statuses <= {"completed", "completed_with_deviation"}:
            status = "completed_with_deviation"
        else:
            status = "failed"
        self.ledger.finalize_claim(
            experiment_id,
            status=status,
            eligible=claim.eligible,
            reasons=list(claim.reasons),
        )
        return self.summary(experiment_id)

    def summary(self, experiment_id: str) -> dict[str, Any]:
        evidence = self.ledger.experiment_evidence(experiment_id)
        experiment = evidence["experiment"]
        return {
            "ok": experiment["status"] in {"completed", "completed_with_deviation"},
            "operation": "research_lab_experiment",
            "experiment_id": experiment["id"],
            "name": experiment["name"],
            "stage": experiment["stage"],
            "status": experiment["status"],
            "protocol_hash": experiment["protocol_hash"],
            "provenance_hash": experiment["provenance_hash"],
            "claim_eligible": bool(experiment["claim_eligible"]),
            "claim_reasons": json.loads(experiment["claim_reasons_json"]),
            "trial_counts": _status_counts(evidence["trials"]),
            "attempt_counts": _status_counts(evidence["attempts"]),
            "metric_count": len(evidence["metrics"]),
            "artifact_count": len(evidence["artifacts"]),
            "open_deviation_count": sum(item["status"] == "open" for item in evidence["deviations"]),
            "state_dir": str(self.state_dir),
            "raw_output_omitted": True,
            "token_omitted": True,
        }


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def run_local_experiment(*, spec: ExperimentSpec, spec_path: Path, state_dir: Path) -> dict[str, Any]:
    if sys.version_info < (3, 11):
        raise RuntimeError("Research Lab requires Python 3.11 or newer")
    return asyncio.run(LocalExperimentRunner(state_dir=state_dir).run(spec=spec, spec_path=spec_path))
