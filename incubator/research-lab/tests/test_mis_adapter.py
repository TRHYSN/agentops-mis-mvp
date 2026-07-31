from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_lab.ledger import ResearchLedger
from research_lab.mis_adapter import build_mis_evidence_bundle, sync_mis_evidence


class MISEvidenceAdapterTests(unittest.TestCase):
    def test_bundle_omits_local_paths_and_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = ResearchLedger(root / "research-lab.db")
            ledger.upsert_experiment(
                experiment_id="exp_test",
                name="bounded",
                stage="smoke",
                protocol_hash="a" * 64,
                provenance_hash="b" * 64,
                protocol_document={"protocol": {"primary_metric": "accuracy"}},
            )
            ledger.ensure_trial(trial_id="trl_test", experiment_id="exp_test", params={"seed": 7})
            ledger.start_attempt(
                attempt_id="att_test",
                trial_id="trl_test",
                attempt_number=1,
                run_dir=root / "private-run-directory",
            )
            ledger.replace_attempt_evidence(
                trial_id="trl_test",
                attempt_id="att_test",
                metrics=[{"name": "accuracy", "value": 0.95, "step": 1, "recorded_at": "now"}],
                artifacts=[{"id": "art_test", "relative_path": "model.json", "sha256": "c" * 64, "size_bytes": 12}],
                deviations=[],
            )
            ledger.finish_attempt(
                attempt_id="att_test",
                status="completed",
                exit_code=0,
                error_summary=None,
                stdout_sha256="d" * 64,
                stderr_sha256="e" * 64,
            )
            ledger.set_trial_status("trl_test", "completed")
            ledger.finalize_claim("exp_test", status="completed", eligible=True, reasons=[])

            bundle = build_mis_evidence_bundle(ledger, "exp_test")
            serialized = json.dumps(bundle, sort_keys=True)
            self.assertEqual(bundle["schema_version"], "research_lab_mis_evidence_v1")
            self.assertEqual(bundle["metrics"][0]["value"], 0.95)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("run_dir", serialized)
            self.assertNotIn("stdout.log", serialized)
            self.assertTrue(bundle["omissions"]["artifact_bodies"])

    def test_remote_sync_fails_closed_before_network(self) -> None:
        with patch("urllib.request.urlopen") as opener:
            with self.assertRaisesRegex(ValueError, "local HTTP Host"):
                sync_mis_evidence({}, base_url="https://example.com")
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
