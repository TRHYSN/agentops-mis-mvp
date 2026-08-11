"""Windows-safe OpenCekura CLI."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from open_cekura.campaigns.service import (
    CampaignServiceError,
    EXIT_BLOCKED,
    EXIT_EVIDENCE_INVALID,
    compare_campaigns,
    evaluate_campaign_gate,
    run_campaign,
    verify_campaign_evidence,
)
from open_cekura.evidence import EvidenceError
from open_cekura.mis import MISBridgeError
from open_cekura.release_gate.policy import ReleaseGateError
from open_cekura.scenarios.loader import ScenarioContractError, load_scenario
from open_cekura.storage import RepositoryError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-cekura", description="OpenCekura Reliability Lab CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser(
        "doctor", help="Check Windows development prerequisites."
    )
    doctor.set_defaults(handler=doctor_command)

    scenario = commands.add_parser(
        "scenario", help="Validate versioned Scenario contracts."
    )
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    validate = scenario_commands.add_parser(
        "validate", help="Validate one Scenario YAML file."
    )
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=scenario_validate)

    campaign = commands.add_parser("campaign", help="Run and compare campaigns.")
    campaign_commands = campaign.add_subparsers(dest="campaign_command", required=True)
    run = campaign_commands.add_parser(
        "run", help="Run a deterministic Scenario suite and persist its evidence."
    )
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--agent", choices=("mock",), required=True)
    run.add_argument(
        "--version",
        choices=("baseline", "candidate"),
        default="candidate",
        help="Mock defect profile (default: candidate).",
    )
    run.add_argument("--campaign-id")
    _add_state_arguments(run)
    run.set_defaults(handler=campaign_run)

    compare = campaign_commands.add_parser(
        "compare", help="Compare verified baseline and candidate campaigns."
    )
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    _add_state_arguments(compare)
    compare.set_defaults(handler=campaign_compare)

    gate = commands.add_parser("gate", help="Evaluate Release Gate policy.")
    gate_commands = gate.add_subparsers(dest="gate_command", required=True)
    evaluate = gate_commands.add_parser(
        "evaluate", help="Recompute a persisted campaign Release Gate."
    )
    evaluate.add_argument("--campaign", required=True)
    evaluate.add_argument("--baseline")
    evaluate.add_argument("--strict-warning", action="store_true")
    _add_state_arguments(evaluate)
    evaluate.set_defaults(handler=gate_evaluate)

    evidence = commands.add_parser("evidence", help="Verify evidence bundles.")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    verify = evidence_commands.add_parser(
        "verify", help="Verify hashes and contracts without rewriting evidence."
    )
    verify.add_argument("--campaign", required=True)
    verify.add_argument("--artifacts", type=Path)
    verify.add_argument("--strict", action="store_true")
    verify.set_defaults(handler=evidence_verify)
    return parser


def _add_state_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default="local-demo")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--artifacts", type=Path)


def doctor_command(_args: argparse.Namespace) -> int:
    from open_cekura.windows import doctor

    report = doctor.run_doctor(repo_root=doctor.find_repo_root(Path.cwd()))
    print(doctor.render_report(report))
    return 0 if report.ok else 1


def scenario_validate(args: argparse.Namespace) -> int:
    source = args.path.resolve()
    scenario = load_scenario(source)
    print(
        json.dumps(
            {
                "ok": True,
                "operation": "scenario_validate",
                "scenario_id": scenario.id,
                "schema_version": scenario.schema_version,
                "source": str(source),
                "token_omitted": True,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def campaign_run(args: argparse.Namespace) -> int:
    payload = run_campaign(
        suite_path=args.suite,
        agent=args.agent,
        version=args.version,
        campaign_id=args.campaign_id,
        workspace_id=args.workspace,
        db_path=args.db,
        artifact_root=args.artifacts,
    )
    _print_json(payload)
    return 0


def campaign_compare(args: argparse.Namespace) -> int:
    payload = compare_campaigns(
        baseline_campaign_id=args.baseline,
        candidate_campaign_id=args.candidate,
        workspace_id=args.workspace,
        db_path=args.db,
        artifact_root=args.artifacts,
    )
    _print_json(payload)
    return EXIT_BLOCKED if payload["release_gate"]["decision"] == "block" else 0


def gate_evaluate(args: argparse.Namespace) -> int:
    payload = evaluate_campaign_gate(
        campaign_id=args.campaign,
        baseline_campaign_id=args.baseline,
        workspace_id=args.workspace,
        db_path=args.db,
        artifact_root=args.artifacts,
    )
    _print_json(payload)
    decision = payload["release_gate"]["decision"]
    if decision == "block" or (decision == "warn" and args.strict_warning):
        return EXIT_BLOCKED
    return 0


def evidence_verify(args: argparse.Namespace) -> int:
    payload = verify_campaign_evidence(
        campaign_id=args.campaign,
        artifact_root=args.artifacts,
        strict=args.strict,
    )
    _print_json(payload)
    return 0 if payload["verified"] else EXIT_EVIDENCE_INVALID


def _print_json(payload: object, *, file=None) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        file=file,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except ScenarioContractError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "operation": "scenario_validate",
                    "error": "scenario_contract_error",
                    "message": str(exc),
                    "token_omitted": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except (
        CampaignServiceError,
        EvidenceError,
        MISBridgeError,
        ReleaseGateError,
        RepositoryError,
        sqlite3.Error,
        OSError,
        ValueError,
    ) as exc:
        code = (
            exc.code
            if isinstance(exc, CampaignServiceError)
            else "database_unavailable"
            if isinstance(exc, sqlite3.Error)
            else "operation_failed"
        )
        message = (
            "The MIS database operation failed."
            if isinstance(exc, sqlite3.Error)
            else str(exc)
        )
        operation = "_".join(
            str(value)
            for value in (
                getattr(args, "command", None),
                getattr(args, f"{getattr(args, 'command', '')}_command", None),
            )
            if value
        )
        _print_json(
            {
                "ok": False,
                "operation": operation or "open_cekura",
                "error": code,
                "message": message,
                "token_omitted": True,
            },
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
