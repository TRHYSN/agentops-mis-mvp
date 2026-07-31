#!/usr/bin/env python3
"""Run the bounded BWFormer Fusion v2 CPU smoke under Research Lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from research_lab.runtime import artifacts_dir, log_metric, record_actuals


REQUIRED_FILES = (
    "MANIFEST.json",
    "train_fusion.py",
    "tools/smoke_test.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(project_root: Path) -> tuple[dict[str, Any], str]:
    manifest_path = project_root / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid BWFormer manifest: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise RuntimeError("BWFormer manifest must contain a files list")
    return manifest, sha256_file(manifest_path)


def verify_manifest(project_root: Path, manifest: dict[str, Any]) -> int:
    verified = 0
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise RuntimeError("BWFormer manifest contains a non-object file entry")
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(relative, str) or not relative or not isinstance(expected, str):
            raise RuntimeError("BWFormer manifest file entries require path and sha256")
        candidate = (project_root / relative).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError as exc:
            raise RuntimeError("BWFormer manifest path escapes the project root") from exc
        if not candidate.is_file() or sha256_file(candidate) != expected:
            raise RuntimeError(f"BWFormer manifest integrity failed for {relative}")
        verified += 1
    return verified


def run_bounded(
    *,
    label: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=False,
        timeout=timeout_seconds,
        check=False,
    )
    duration = time.monotonic() - started
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    result = {
        "label": label,
        "exit_code": completed.returncode,
        "duration_seconds": round(duration, 6),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
    }
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit code {completed.returncode}; "
            "raw child output was omitted"
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--config", default="configs/fusion_24g_safe.yaml")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--metric-code-hash", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise RuntimeError("BWFormer project root does not exist")
    for relative in REQUIRED_FILES:
        if not (project_root / relative).is_file():
            raise RuntimeError(f"BWFormer project is missing {relative}")

    config_path = (project_root / args.config).resolve()
    try:
        config_path.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError("BWFormer config must stay under the project root") from exc
    if not config_path.is_file():
        raise RuntimeError("BWFormer config does not exist")

    started = time.monotonic()
    manifest, manifest_sha256 = load_manifest(project_root)
    verified_files = verify_manifest(project_root, manifest)
    log_metric("manifest_integrity_pass", 1.0, step=0)
    log_metric("manifest_files_verified", float(verified_files), step=0)

    child_env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH"}
    }
    child_env["PYTHONHASHSEED"] = str(args.seed)
    config_result = run_bounded(
        label="fusion_config_dry_run",
        command=[
            sys.executable,
            "train_fusion.py",
            "--fusion-config",
            args.config,
            "--fusion-dry-run",
        ],
        cwd=project_root,
        env=child_env,
        timeout_seconds=args.timeout_seconds,
    )
    log_metric("config_dry_run_pass", 1.0, step=1)

    deterministic_smoke = (
        "import os, random, runpy; "
        "seed=int(os.environ['PYTHONHASHSEED']); "
        "random.seed(seed); "
        "import numpy as np; np.random.seed(seed); "
        "import torch; torch.manual_seed(seed); "
        "runpy.run_path('tools/smoke_test.py', run_name='__main__')"
    )
    smoke_result = run_bounded(
        label="fusion_component_cpu_smoke",
        command=[sys.executable, "-c", deterministic_smoke],
        cwd=project_root,
        env=child_env,
        timeout_seconds=args.timeout_seconds,
    )
    log_metric("component_smoke_pass", 1.0, step=2)
    log_metric("bwformer_cpu_smoke_pass", 1.0, step=2)

    duration = time.monotonic() - started
    log_metric("smoke_duration_seconds", duration, step=2)
    summary = {
        "schema_version": 1,
        "project": "BWformer1-fusion-v2",
        "manifest_sha256": manifest_sha256,
        "manifest_files_verified": verified_files,
        "config_relative_path": args.config,
        "config_sha256": sha256_file(config_path),
        "seed": args.seed,
        "gpu_execution_performed": False,
        "dataset_opened": False,
        "raw_child_output_omitted": True,
        "checks": [config_result, smoke_result],
    }
    summary_path = artifacts_dir() / "bwformer_fusion_smoke_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    record_actuals(
        initialization_mode="no_checkpoint",
        training_scope="fusion_v2_component_cpu_smoke",
        dataset_version="synthetic-geometry-fixture-v1",
        model_architecture="bwformer-fusion-v2-component-suite",
        checkpoint_selection_rule="not_applicable_smoke",
        metric_code_hash=args.metric_code_hash,
        precision="float32",
        seed=args.seed,
        manifest_sha256=manifest_sha256,
        config_sha256=summary["config_sha256"],
        gpu_execution_performed=False,
        dataset_opened=False,
        raw_child_output_omitted=True,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "operation": "bwformer_fusion_cpu_smoke",
                "manifest_files_verified": verified_files,
                "gpu_execution_performed": False,
                "dataset_opened": False,
                "raw_child_output_omitted": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
