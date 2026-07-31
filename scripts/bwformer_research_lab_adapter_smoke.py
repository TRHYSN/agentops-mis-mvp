#!/usr/bin/env python3
"""Exercise the BWFormer adapter through a fixture-only Research Lab Trial."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = ROOT / "incubator" / "research-lab"
PREPARE = ROOT / "scripts" / "prepare_bwformer_research_lab_smoke.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def parse_json(process: subprocess.CompletedProcess[str]) -> dict:
    require(process.returncode == 0, process.stderr or process.stdout)
    value = json.loads(process.stdout)
    require(isinstance(value, dict), "command output must be a JSON object")
    return value


def write_fixture(root: Path) -> None:
    (root / "configs").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "configs" / "fusion_24g_safe.yaml").write_text(
        "runtime:\n  amp: false\n",
        encoding="utf-8",
    )
    (root / "train_fusion.py").write_text(
        """#!/usr/bin/env python3
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--fusion-config")
parser.add_argument("--fusion-dry-run", action="store_true")
args = parser.parse_args()
assert args.fusion_dry_run
print("fixture config validated")
""",
        encoding="utf-8",
    )
    (root / "tools" / "smoke_test.py").write_text(
        """import numpy as np
import torch
assert np.SMOKE_FIXTURE is True
assert torch.SMOKE_FIXTURE is True
print("fixture component smoke passed")
""",
        encoding="utf-8",
    )
    (root / "numpy.py").write_text(
        """SMOKE_FIXTURE = True
class _Random:
    @staticmethod
    def seed(value):
        return None
random = _Random()
""",
        encoding="utf-8",
    )
    (root / "torch.py").write_text(
        """SMOKE_FIXTURE = True
def manual_seed(value):
    return None
""",
        encoding="utf-8",
    )
    (root / "scipy.py").write_text("SMOKE_FIXTURE = True\n", encoding="utf-8")
    (root / "yaml.py").write_text("SMOKE_FIXTURE = True\n", encoding="utf-8")
    files = []
    for relative in (
        "configs/fusion_24g_safe.yaml",
        "numpy.py",
        "scipy.py",
        "torch.py",
        "tools/smoke_test.py",
        "train_fusion.py",
        "yaml.py",
    ):
        path = root / relative
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "name": "BWformer1-fusion-v2",
                "version": "fixture",
                "files": files,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def main() -> int:
    python = Path(os.environ.get("BWFORMER_SMOKE_PYTHON", sys.executable)).absolute()
    with tempfile.TemporaryDirectory(prefix="bwformer-research-lab-") as tmp:
        temp = Path(tmp)
        project = temp / "bwformer"
        project.mkdir()
        write_fixture(project)
        spec_path = temp / "bwformer-smoke.json"
        state_dir = temp / "state"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(RESEARCH_ROOT)

        prepared = parse_json(
            run(
                [
                    sys.executable,
                    str(PREPARE),
                    "--project-root",
                    str(project),
                    "--python",
                    str(python),
                    "--output",
                    str(spec_path),
                ],
                cwd=ROOT,
                env=env,
            )
        )
        require(prepared.get("manifest_files_verified") == 7, "fixture manifest was not verified")
        require(
            prepared.get("verified_runtime_modules") == ["numpy", "scipy", "yaml", "torch"],
            "fixture runtime inventory was not verified",
        )
        require(prepared.get("private_dataset_opened") is False, "fixture opened a private dataset")
        generated_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        require(
            generated_spec["command"][0] == str(python),
            "spec generator resolved away the configured virtual environment",
        )

        dry_run = parse_json(
            run(
                [
                    sys.executable,
                    "-m",
                    "research_lab",
                    "run-local",
                    "--spec",
                    str(spec_path),
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=RESEARCH_ROOT,
                env=env,
            )
        )
        require(dry_run.get("dry_run") is True, "default run must remain dry-run")
        require(dry_run.get("execution_performed") is False, "dry-run executed the adapter")

        completed = parse_json(
            run(
                [
                    sys.executable,
                    "-m",
                    "research_lab",
                    "run-local",
                    "--spec",
                    str(spec_path),
                    "--state-dir",
                    str(state_dir),
                    "--confirm-run",
                ],
                cwd=RESEARCH_ROOT,
                env=env,
            )
        )
        require(completed.get("status") == "completed", f"unexpected status: {completed}")
        require(completed.get("trial_counts") == {"completed": 1}, "Trial did not complete")
        require(completed.get("metric_count") == 6, "bounded metric count changed")
        require(completed.get("artifact_count") == 1, "summary artifact was not hashed")
        require(completed.get("open_deviation_count") == 0, "protocol deviation detected")
        require(completed.get("claim_eligible") is False, "smoke must not become claim eligible")

        result = {
            "ok": True,
            "operation": "bwformer_research_lab_adapter_smoke",
            "dry_run_default_preserved": True,
            "fixture_trial_completed": True,
            "external_project_execution": False,
            "fixture_only": True,
            "trial_counts": completed["trial_counts"],
            "metric_count": completed["metric_count"],
            "artifact_count": completed["artifact_count"],
            "open_deviation_count": completed["open_deviation_count"],
            "claim_eligible": completed["claim_eligible"],
            "private_dataset_opened": False,
            "raw_child_output_omitted": True,
            "token_omitted": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError) as exc:
        print(f"bwformer_research_lab_adapter_smoke FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
