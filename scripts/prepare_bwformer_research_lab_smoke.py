#!/usr/bin/env python3
"""Prepare a local-only Research Lab spec for a BWFormer Fusion v2 overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = (
    ROOT
    / "incubator"
    / "research-lab"
    / "research_lab"
    / "bwformer_adapter.py"
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
        raise ValueError(f"cannot read BWFormer MANIFEST.json: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("BWFormer MANIFEST.json must contain a files list")
    return manifest, sha256_file(manifest_path)


def verify_manifest(project_root: Path, manifest: dict[str, Any]) -> int:
    verified = 0
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise ValueError("BWFormer manifest contains a non-object file entry")
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("BWFormer manifest entries require path and sha256")
        candidate = (project_root / relative).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("BWFormer manifest path escapes the project root") from exc
        if not candidate.is_file() or sha256_file(candidate) != expected:
            raise ValueError(f"BWFormer manifest integrity failed for {relative}")
        verified += 1
    return verified


def python_version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ValueError("configured Python executable could not report its version")
    return completed.stdout.strip()


def verify_python_runtime(executable: Path, project_root: Path) -> list[str]:
    required = ["numpy", "scipy", "yaml", "torch"]
    probe = (
        "import importlib.util, json; "
        f"required={required!r}; "
        "print(json.dumps({name: bool(importlib.util.find_spec(name)) for name in required}))"
    )
    completed = subprocess.run(
        [str(executable), "-c", probe],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("configured Python executable could not inspect its modules")
    try:
        availability = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("configured Python executable returned invalid module inventory") from exc
    missing = [name for name in required if availability.get(name) is not True]
    if missing:
        raise ValueError(
            "BWFormer CPU smoke runtime is missing modules: " + ", ".join(missing)
        )
    return required


def build_spec(
    *,
    project_root: Path,
    python: Path,
    config_relative: str,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, manifest_sha256 = load_manifest(project_root)
    verified_files = verify_manifest(project_root, manifest)
    config_path = (project_root / config_relative).resolve()
    try:
        config_path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("config must stay under the BWFormer project root") from exc
    if not config_path.is_file():
        raise ValueError("BWFormer config does not exist")
    if not python.is_file():
        raise ValueError("Python executable does not exist")
    runtime_modules = verify_python_runtime(python, project_root)
    adapter_sha256 = sha256_file(ADAPTER)
    metric_code_hash = f"sha256:{adapter_sha256}"
    revision = f"fusion-v2-overlay-{manifest.get('version', 'unknown')}-{manifest_sha256[:16]}"
    resolved_config = {
        "adapter_sha256": adapter_sha256,
        "config_relative_path": config_relative,
        "config_sha256": sha256_file(config_path),
        "manifest_sha256": manifest_sha256,
        "seed": seed,
        "target_hardware_gate": "single-cuda-gpu-24gb-not-executed",
        "verified_runtime_modules": runtime_modules,
    }
    spec = {
        "name": "bwformer-fusion-v2-local-cpu-smoke",
        "stage": "smoke",
        "executor": "local",
        "command": [
            str(python),
            "-m",
            "research_lab.bwformer_adapter",
            "--project-root",
            str(project_root),
            "--config",
            config_relative,
            "--seed",
            "{seed}",
            "--metric-code-hash",
            metric_code_hash,
        ],
        "matrix": {"seed": [seed]},
        "max_concurrency": 1,
        "timeout_seconds": 300,
        "retries": 0,
        "workdir": str(project_root),
        "environment": {},
        "integrity": {
            "strict_actuals": True,
            "required_actual_fields": [
                "initialization_mode",
                "training_scope",
                "dataset_version",
                "model_architecture",
                "checkpoint_selection_rule",
                "metric_code_hash",
                "precision",
            ],
            "minimum_completed_trials": 1,
            "minimum_distinct_seeds": 1,
            "minimum_primary_metric": 1.0,
            "allow_warning_deviations": False,
            "require_provenance": False,
        },
        "protocol": {
            "research_question": (
                "Does the manifest-verified BWFormer Fusion v2 overlay pass its "
                "declared configuration and deterministic CPU component smoke checks?"
            ),
            "primary_metric": "bwformer_cpu_smoke_pass",
            "initialization_mode": "no_checkpoint",
            "training_scope": "fusion_v2_component_cpu_smoke",
            "dataset_version": "synthetic-geometry-fixture-v1",
            "model_architecture": "bwformer-fusion-v2-component-suite",
            "checkpoint_selection_rule": "not_applicable_smoke",
            "metric_code_hash": metric_code_hash,
            "precision": "float32",
            "hardware_boundary": "GPU/full-model training not executed by this smoke",
            "claim_policy": "exploratory smoke evidence only; never a model-quality claim",
        },
        "provenance": {
            "code": {
                "repository": "https://github.com/geogejoy107-jpg/BWformer1",
                "revision": revision,
                "dirty": False,
            },
            "datasets": [
                {
                    "name": "synthetic-geometry-fixture",
                    "uri": "generator://bwformer-fusion-v2/tools/smoke-test",
                    "version": "synthetic-geometry-fixture-v1",
                    "metadata": {
                        "raw_samples_persisted": False,
                        "private_dataset_opened": False,
                    },
                }
            ],
            "models": [],
            "resolved_config": resolved_config,
            "environment": {
                "name": "bwformer-cpu-smoke",
                "uri": "python.org/cpython",
                "version": python_version(python),
                "metadata": {
                    "gpu_required": False,
                    "local_only": True,
                    "network_access_required": False,
                },
            },
        },
    }
    public_summary = {
        "project": str(project_root),
        "python": str(python),
        "config": config_relative,
        "manifest_version": manifest.get("version"),
        "manifest_sha256": manifest_sha256,
        "manifest_files_verified": verified_files,
        "metric_code_hash": metric_code_hash,
        "verified_runtime_modules": runtime_modules,
        "private_dataset_opened": False,
        "credentials_required": False,
    }
    return spec, public_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--config", default="configs/fusion_24g_safe.yaml")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    # Preserve a virtual environment's interpreter symlink. Resolving it would
    # bypass pyvenv.cfg and silently fall back to the base Python environment.
    python = Path(args.python).expanduser().absolute()
    output = Path(args.output).expanduser().resolve()
    spec, summary = build_spec(
        project_root=project_root,
        python=python,
        config_relative=args.config,
        seed=args.seed,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "operation": "prepare_bwformer_research_lab_smoke",
                "spec_path": str(output),
                **summary,
                "raw_training_data_omitted": True,
                "token_omitted": True,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "token_omitted": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
