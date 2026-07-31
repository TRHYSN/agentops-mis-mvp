from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from research_lab.ledger import ResearchLedger
from research_lab.orchestrator import run_local_experiment
from research_lab.protocol import ExperimentSpec


TRAIN_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

seed = int(sys.argv[1])
metrics_path = Path(os.environ["RESEARCH_LAB_METRICS_PATH"])
metrics_path.write_text(
    "\n".join(
        json.dumps({"name": "final_accuracy", "value": value, "step": step})
        for step, value in [(1, 0.75), (2, 0.95 + seed * 0.0)]
    ) + "\n",
    encoding="utf-8",
)
actuals = {
    "initialization_mode": "from_scratch",
    "training_scope": "full_model",
    "dataset_version": "synthetic-v1",
    "model_architecture": "tiny_mlp",
    "protocol_hash": os.environ["RESEARCH_LAB_PROTOCOL_HASH"],
    "provenance_hash": os.environ["RESEARCH_LAB_PROVENANCE_HASH"],
    "resolved_config_hash": os.environ["RESEARCH_LAB_RESOLVED_CONFIG_HASH"],
    "code_revision": os.environ["RESEARCH_LAB_CODE_REVISION"],
}
Path(os.environ["RESEARCH_LAB_ACTUALS_PATH"]).write_text(
    json.dumps(actuals, sort_keys=True),
    encoding="utf-8",
)
artifact_dir = Path(os.environ["RESEARCH_LAB_ARTIFACTS_DIR"])
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "model.json").write_text(
    json.dumps({"architecture": "tiny_mlp", "seed": seed}, sort_keys=True),
    encoding="utf-8",
)
"""

FAIL_ONCE_SCRIPT = r"""
import sys
from pathlib import Path

seed = int(sys.argv[1])
marker = Path(f"failed-once-{seed}")
if not marker.exists():
    marker.write_text("failed", encoding="utf-8")
    raise SystemExit(3)
""" + TRAIN_SCRIPT


class OrchestratorTests(unittest.TestCase):
    def _spec(self) -> dict[str, object]:
        return {
            "name": "tiny-mlp-orchestrator-test",
            "stage": "confirmatory",
            "executor": "local",
            "command": [sys.executable, "train.py", "{seed}"],
            "matrix": {"seed": [11, 22]},
            "max_concurrency": 2,
            "timeout_seconds": 10,
            "retries": 0,
            "workdir": ".",
            "environment": {},
            "integrity": {
                "strict_actuals": True,
                "required_actual_fields": [
                    "initialization_mode",
                    "training_scope",
                    "dataset_version",
                ],
                "minimum_completed_trials": 2,
                "minimum_distinct_seeds": 2,
                "minimum_primary_metric": 0.9,
                "allow_warning_deviations": False,
                "require_provenance": True,
            },
            "protocol": {
                "research_question": "Can the local runner preserve a tiny MLP experiment?",
                "primary_metric": "final_accuracy",
                "initialization_mode": "from_scratch",
                "training_scope": "full_model",
                "dataset_version": "synthetic-v1",
                "model_architecture": "tiny_mlp",
            },
            "provenance": {
                "code": {
                    "repository": "https://example.invalid/tiny-mlp.git",
                    "revision": "tiny-mlp-test-v1",
                    "dirty": False,
                },
                "datasets": [
                    {
                        "name": "synthetic",
                        "uri": "generated://tiny-mlp",
                        "version": "synthetic-v1",
                    }
                ],
                "models": [],
                "environment": {
                    "name": "python",
                    "uri": "python-runtime",
                    "version": "test",
                },
                "resolved_config": {"epochs": 2, "hidden_size": 4},
            },
        }

    def test_confirmed_local_run_records_idempotent_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "train.py").write_text(TRAIN_SCRIPT, encoding="utf-8")
            spec_path = root / "experiment.json"
            spec_path.write_text(json.dumps(self._spec()), encoding="utf-8")
            state_dir = root / "state"
            spec = ExperimentSpec.from_dict(self._spec())

            first = run_local_experiment(spec=spec, spec_path=spec_path, state_dir=state_dir)
            second = run_local_experiment(spec=spec, spec_path=spec_path, state_dir=state_dir)

            self.assertTrue(first["ok"])
            self.assertTrue(first["claim_eligible"])
            self.assertEqual(first["trial_counts"], {"completed": 2})
            self.assertEqual(first["attempt_counts"], {"completed": 2})
            self.assertEqual(first["metric_count"], 4)
            self.assertEqual(first["artifact_count"], 2)
            self.assertEqual(first["open_deviation_count"], 0)
            self.assertEqual(second["attempt_counts"], {"completed": 2})

            evidence = ResearchLedger(state_dir / "research-lab.db").experiment_evidence(first["experiment_id"])
            self.assertEqual(len(evidence["attempts"]), 2)
            self.assertTrue(all(item["stdout_sha256"] for item in evidence["attempts"]))
            self.assertTrue(all(item["stderr_sha256"] for item in evidence["attempts"]))

    def test_claim_gate_rejects_primary_metric_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "train.py").write_text(TRAIN_SCRIPT, encoding="utf-8")
            raw = self._spec()
            raw["integrity"]["minimum_primary_metric"] = 0.99
            spec_path = root / "experiment.json"
            spec_path.write_text(json.dumps(raw), encoding="utf-8")

            result = run_local_experiment(
                spec=ExperimentSpec.from_dict(raw),
                spec_path=spec_path,
                state_dir=root / "state",
            )

            self.assertTrue(result["ok"])
            self.assertFalse(result["claim_eligible"])
            self.assertTrue(any("must be >= 0.99" in reason for reason in result["claim_reasons"]))

    def test_failed_trials_keep_attempt_one_before_successful_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "train.py").write_text(FAIL_ONCE_SCRIPT, encoding="utf-8")
            raw = self._spec()
            spec_path = root / "experiment.json"
            spec_path.write_text(json.dumps(raw), encoding="utf-8")
            state_dir = root / "state"
            spec = ExperimentSpec.from_dict(raw)

            first = run_local_experiment(spec=spec, spec_path=spec_path, state_dir=state_dir)
            second = run_local_experiment(spec=spec, spec_path=spec_path, state_dir=state_dir)

            self.assertFalse(first["ok"])
            self.assertEqual(first["attempt_counts"], {"failed": 2})
            self.assertTrue(second["ok"])
            self.assertTrue(second["claim_eligible"])
            self.assertEqual(second["attempt_counts"], {"completed": 2, "failed": 2})
            evidence = ResearchLedger(state_dir / "research-lab.db").experiment_evidence(second["experiment_id"])
            self.assertEqual(
                sorted((item["trial_id"], item["attempt_number"], item["status"]) for item in evidence["attempts"]),
                sorted(
                    (trial["id"], attempt_number, status)
                    for trial in evidence["trials"]
                    for attempt_number, status in ((1, "failed"), (2, "completed"))
                ),
            )
            self.assertGreater(len(evidence["deviations"]), 0)
            self.assertTrue(all(item["status"] == "superseded" for item in evidence["deviations"]))


if __name__ == "__main__":
    unittest.main()
