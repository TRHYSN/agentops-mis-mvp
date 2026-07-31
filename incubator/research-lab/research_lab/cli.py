from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .mis_adapter import build_mis_evidence_bundle, sync_mis_evidence
from .orchestrator import LocalExperimentRunner, run_local_experiment
from .protocol import ExperimentSpec, SpecError
from .resources import runtime_fingerprint
from .server_profiles import ServerProfileError, ServerRegistry


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {source}: {exc}") from exc


def _cmd_validate_spec(args: argparse.Namespace) -> int:
    raw = _load_json(args.spec)
    registry = ServerRegistry.load(args.servers) if args.servers else None
    spec = ExperimentSpec.from_dict(raw)
    profile = None
    if spec.executor == "ssh":
        if registry is None:
            raise SpecError("ssh specs require --servers for profile validation")
        profile = registry.get(str((spec.executor_config or {}).get("profile")))
    payload = {
        "ok": True,
        "operation": "validate_spec",
        "spec": str(Path(args.spec)),
        "name": spec.name,
        "stage": spec.stage.value,
        "executor": spec.executor,
        "trial_count": len(spec.expand_trials()),
        "protocol_hash": spec.protocol_hash,
        "provenance_hash": spec.provenance_hash,
        "server_profile": None if profile is None else {
            "name": profile.name,
            "snapshot_hash": profile.snapshot_hash,
        },
        "token_omitted": True,
    }
    _print_json(payload)
    return 0


def _cmd_server_list(args: argparse.Namespace) -> int:
    registry = ServerRegistry.load(args.servers)
    payload = {
        "ok": True,
        "operation": "server_list",
        **registry.public_payload(),
        "token_omitted": True,
    }
    _print_json(payload)
    return 0


def _cmd_server_probe(args: argparse.Namespace) -> int:
    registry = ServerRegistry.load(args.servers)
    profile = registry.get(args.profile)
    payload = {
        "ok": True,
        "operation": "server_probe",
        "profile": profile.public_snapshot(),
        "snapshot_hash": profile.snapshot_hash,
        "local_references": profile.local_reference_status(),
        "runtime_fingerprint": runtime_fingerprint(Path.cwd()) if args.local_fingerprint else None,
        "network_probe_performed": False,
        "ssh_command_executed": False,
        "token_omitted": True,
    }
    _print_json(payload)
    return 0


def _cmd_inventory(args: argparse.Namespace) -> int:
    _print_json({
        "ok": True,
        "operation": "inventory",
        "runtime_fingerprint": runtime_fingerprint(args.workdir or Path.cwd()),
        "token_omitted": True,
    })
    return 0


def _cmd_run_local(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec).resolve()
    spec = ExperimentSpec.from_dict(_load_json(spec_path))
    state_dir = Path(args.state_dir).resolve()
    if spec.executor != "local":
        raise SpecError("run-local requires executor='local'")
    if not args.confirm_run:
        _print_json(
            {
                "ok": True,
                "operation": "run_local_plan",
                "dry_run": True,
                "execution_performed": False,
                "name": spec.name,
                "stage": spec.stage.value,
                "trial_count": len(spec.expand_trials()),
                "max_concurrency": spec.max_concurrency,
                "protocol_hash": spec.protocol_hash,
                "provenance_hash": spec.provenance_hash,
                "state_dir": str(state_dir),
                "confirm_flag": "--confirm-run",
                "token_omitted": True,
            }
        )
        return 0
    payload = run_local_experiment(spec=spec, spec_path=spec_path, state_dir=state_dir)
    payload["dry_run"] = False
    payload["execution_performed"] = True
    _print_json(payload)
    return 0 if payload["ok"] else 1


def _cmd_show(args: argparse.Namespace) -> int:
    runner = LocalExperimentRunner(state_dir=Path(args.state_dir))
    try:
        payload = runner.summary(args.experiment_id)
    except KeyError as exc:
        raise ValueError(f"unknown experiment: {args.experiment_id}") from exc
    payload["operation"] = "research_lab_show"
    _print_json(payload)
    return 0


def _cmd_sync_mis(args: argparse.Namespace) -> int:
    runner = LocalExperimentRunner(state_dir=Path(args.state_dir))
    bundle = build_mis_evidence_bundle(
        runner.ledger,
        args.experiment_id,
        workspace_id=args.workspace_id,
    )
    if not args.confirm_sync:
        _print_json(
            {
                "ok": True,
                "operation": "research_lab_mis_sync_plan",
                "dry_run": True,
                "sync_performed": False,
                "experiment_id": args.experiment_id,
                "evidence_hash": bundle["evidence_hash"],
                "trial_count": len(bundle["trials"]),
                "metric_count": len(bundle["metrics"]),
                "artifact_count": len(bundle["artifacts"]),
                "confirm_flag": "--confirm-sync",
                "raw_output_omitted": True,
                "token_omitted": True,
            }
        )
        return 0
    payload = sync_mis_evidence(bundle, base_url=args.base_url)
    payload["operation"] = "research_lab_mis_sync"
    payload["dry_run"] = False
    payload["sync_performed"] = True
    _print_json(payload)
    return 0 if payload.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-lab", description="Local-first Research Lab incubator CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-spec", help="Validate an experiment JSON spec.")
    validate.add_argument("--spec", required=True)
    validate.add_argument("--servers", help="Required for ssh executor specs.")
    validate.set_defaults(func=_cmd_validate_spec)

    server_list = sub.add_parser("server-list", help="List non-secret SSH server profiles.")
    server_list.add_argument("--servers", required=True)
    server_list.set_defaults(func=_cmd_server_list)

    server_probe = sub.add_parser("server-probe", help="Inspect a server profile without opening SSH.")
    server_probe.add_argument("--servers", required=True)
    server_probe.add_argument("--profile", required=True)
    server_probe.add_argument("--local-fingerprint", action="store_true")
    server_probe.set_defaults(func=_cmd_server_probe)

    inventory = sub.add_parser("inventory", help="Print local runtime inventory.")
    inventory.add_argument("--workdir")
    inventory.set_defaults(func=_cmd_inventory)

    run_local = sub.add_parser("run-local", help="Plan or execute a local experiment.")
    run_local.add_argument("--spec", required=True)
    run_local.add_argument("--state-dir", default=".research-lab")
    run_local.add_argument("--confirm-run", action="store_true")
    run_local.set_defaults(func=_cmd_run_local)

    show = sub.add_parser("show", help="Show a bounded experiment evidence summary.")
    show.add_argument("--experiment-id", required=True)
    show.add_argument("--state-dir", default=".research-lab")
    show.set_defaults(func=_cmd_show)

    sync_mis = sub.add_parser("sync-mis", help="Plan or publish bounded evidence to a local AgentOps MIS Host.")
    sync_mis.add_argument("--experiment-id", required=True)
    sync_mis.add_argument("--state-dir", default=".research-lab")
    sync_mis.add_argument("--workspace-id", default="local-demo")
    sync_mis.add_argument("--base-url", default="http://127.0.0.1:8787")
    sync_mis.add_argument("--confirm-sync", action="store_true")
    sync_mis.set_defaults(func=_cmd_sync_mis)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (SpecError, ServerProfileError, ValueError) as exc:
        _print_json({
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
            "token_omitted": True,
        })
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
