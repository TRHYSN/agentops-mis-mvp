"""Build and compare byte-reproducible OpenCekura distributions in CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
import re
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agentops_mis_cli import _build_backend as backend  # noqa: E402


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output: Path, result: Path) -> int:
    output = output.resolve()
    result = result.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result.parent.mkdir(parents=True, exist_ok=True)
    wheel = output / backend.build_wheel(str(output))
    sdist = output / backend.build_sdist(str(output))
    payload = {
        "schema_version": 1,
        "source_commit_sha": backend._source_commit_sha(),
        "os": platform.system(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "artifacts": {
            wheel.name: _sha256(wheel),
            sdist.name: _sha256(sdist),
        },
    }
    result.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(result)
    return 0


def _downloaded_artifact(record_path: Path, name: str) -> Path:
    if not name or Path(name).name != name:
        raise ValueError(f"invalid distribution artifact name: {name!r}")
    candidates = [
        path
        for path in record_path.parent.rglob("*")
        if path.name == name and path.is_file() and not path.is_symlink()
    ]
    if not candidates:
        raise ValueError(f"downloaded artifact missing: {name}")
    if len(candidates) != 1:
        raise ValueError(f"downloaded artifact is ambiguous: {name}")
    return candidates[0]


def verify(inputs: Path, *, expected_commit: str) -> int:
    expected = str(expected_commit).strip().lower()
    if not _COMMIT.fullmatch(expected):
        raise ValueError("expected source commit must be one 40-character SHA")
    records = []
    for path in sorted(inputs.resolve().rglob("distribution-digest-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        artifacts = record.get("artifacts", {})
        if not isinstance(artifacts, dict):
            raise ValueError("distribution record artifacts must be an object")
        for name, recorded_digest in artifacts.items():
            if not isinstance(name, str) or not _SHA256.fullmatch(
                str(recorded_digest)
            ):
                raise ValueError("distribution record contains an invalid artifact")
            archive = _downloaded_artifact(path, name)
            if _sha256(archive) != recorded_digest:
                raise ValueError(f"downloaded artifact digest mismatch: {name}")
        records.append(record)
    if len(records) != 4:
        raise ValueError(f"expected 4 distribution digest records, found {len(records)}")
    identities = {(record.get("os"), record.get("python_version")) for record in records}
    expected_identities = {
        ("Linux", "3.10"),
        ("Linux", "3.11"),
        ("Windows", "3.10"),
        ("Windows", "3.11"),
    }
    if identities != expected_identities:
        raise ValueError(f"distribution matrix is incomplete: {sorted(identities)!r}")
    commits = {record.get("source_commit_sha") for record in records}
    if len(commits) != 1 or not _COMMIT.fullmatch(str(next(iter(commits), ""))):
        raise ValueError("distribution records do not identify one exact source commit")
    if commits != {expected}:
        raise ValueError("distribution records do not match the expected source commit")
    artifact_names = {tuple(sorted(record.get("artifacts", {}))) for record in records}
    if len(artifact_names) != 1 or len(next(iter(artifact_names), ())) != 2:
        raise ValueError("distribution records do not cover the same wheel and sdist")
    names = next(iter(artifact_names))
    for name in names:
        digests = {record["artifacts"].get(name) for record in records}
        if len(digests) != 1 or not _SHA256.fullmatch(str(next(iter(digests), ""))):
            raise ValueError(f"cross-platform distribution mismatch: {name}")
    print(
        json.dumps(
            {
                "ok": True,
                "source_commit_sha": next(iter(commits)),
                "matrix": sorted(f"{os_name}-py{python}" for os_name, python in identities),
                "artifacts": records[0]["artifacts"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--output", type=Path, required=True)
    build_parser.add_argument("--result", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--inputs", type=Path, required=True)
    verify_parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    try:
        return (
            build(args.output, args.result)
            if args.command == "build"
            else verify(args.inputs, expected_commit=args.expected_commit)
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"distribution audit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
