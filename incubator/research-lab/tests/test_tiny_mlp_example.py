from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from research_lab.integrity import compare_actuals
from research_lab.protocol import ExperimentSpec


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "examples" / "tiny_mlp_experiment.json"
SCRIPT_PATH = ROOT / "examples" / "tiny_mlp_train.py"


class TinyMLPExampleTests(unittest.TestCase):
    def test_spec_and_both_seed_runs_emit_governed_evidence(self) -> None:
        spec = ExperimentSpec.from_dict(json.loads(SPEC_PATH.read_text(encoding="utf-8")))
        self.assertEqual(spec.stage.value, "confirmatory")
        self.assertEqual(spec.executor, "local")
        self.assertEqual(spec.expand_trials(), [{"seed": 17}, {"seed": 29}])
        self.assertEqual(spec.protocol["primary_metric"], "validation_accuracy")
        self.assertIsNotNone(spec.provenance)
        assert spec.provenance is not None

        for seed in (17, 29):
            with self.subTest(seed=seed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                metrics_path = root / "evidence" / "metrics.jsonl"
                actuals_path = root / "evidence" / "actuals.json"
                artifact_path = root / "artifacts"
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": str(ROOT),
                    "PYTHONIOENCODING": "utf-8",
                    "RESEARCH_LAB_METRICS_PATH": str(metrics_path),
                    "RESEARCH_LAB_ACTUALS_PATH": str(actuals_path),
                    "RESEARCH_LAB_ARTIFACTS_DIR": str(artifact_path),
                    "RESEARCH_LAB_PROTOCOL_HASH": spec.protocol_hash,
                    "RESEARCH_LAB_PROVENANCE_HASH": spec.provenance_hash or "",
                    "RESEARCH_LAB_RESOLVED_CONFIG_HASH": (
                        spec.provenance.resolved_config_hash or ""
                    ),
                    "RESEARCH_LAB_CODE_REVISION": spec.provenance.code.revision,
                    "RESEARCH_LAB_EXPERIMENT_ID": "exp_tiny_mlp_test",
                    "RESEARCH_LAB_TRIAL_ID": f"trial_seed_{seed}",
                    "RESEARCH_LAB_ATTEMPT_ID": f"attempt_seed_{seed}",
                }
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), "--seed", str(seed)],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                output = json.loads(completed.stdout)
                self.assertTrue(output["ok"])
                self.assertEqual(output["seed"], seed)
                self.assertGreaterEqual(output["validation_accuracy"], 0.85)
                self.assertTrue(output["raw_samples_omitted"])
                self.assertTrue(output["credentials_omitted"])

                metrics = [
                    json.loads(line)
                    for line in metrics_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertGreaterEqual(len(metrics), 4)
                self.assertIn("train_loss", {item["name"] for item in metrics})
                self.assertIn("validation_accuracy", {item["name"] for item in metrics})
                final_accuracy = [
                    item
                    for item in metrics
                    if item["name"] == "validation_accuracy" and item["step"] == 250
                ]
                self.assertEqual(len(final_accuracy), 1)
                self.assertGreaterEqual(final_accuracy[0]["value"], 0.85)

                actuals = json.loads(actuals_path.read_text(encoding="utf-8"))
                self.assertEqual(actuals["seed"], seed)
                self.assertEqual(actuals["protocol_hash"], spec.protocol_hash)
                self.assertEqual(actuals["provenance_hash"], spec.provenance_hash)
                self.assertEqual(
                    actuals["resolved_config_hash"],
                    spec.provenance.resolved_config_hash,
                )
                self.assertEqual(actuals["code_revision"], spec.provenance.code.revision)
                self.assertEqual(actuals["dataset_version"], "synthetic-xor-v1")
                self.assertEqual(actuals["model_architecture"], "mlp-2x8x2-tanh-softmax")
                self.assertTrue(actuals["raw_samples_omitted"])
                self.assertTrue(actuals["credentials_omitted"])
                self.assertEqual(
                    compare_actuals(spec.protocol_document, actuals, spec.integrity),
                    [],
                )

                model_path = artifact_path / "tiny_mlp_model.json"
                summary_path = artifact_path / "tiny_mlp_summary.json"
                self.assertTrue(model_path.is_file())
                self.assertTrue(summary_path.is_file())
                model = json.loads(model_path.read_text(encoding="utf-8"))
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                self.assertEqual(model["format"], "research-lab-safe-mlp-v1")
                self.assertEqual(model["seed"], seed)
                self.assertEqual(len(model["weights_input"]), 8)
                self.assertEqual(len(model["weights_output"]), 2)
                self.assertEqual(summary["seed"], seed)
                self.assertEqual(summary["model_sha256"], actuals["model_sha256"])
                self.assertNotIn("samples", model)
                self.assertNotIn("samples", summary)


if __name__ == "__main__":
    unittest.main()
