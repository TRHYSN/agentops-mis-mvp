"""Windows-safe OpenCekura CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from open_cekura.scenarios.loader import ScenarioContractError, load_scenario


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-cekura", description="OpenCekura Reliability Lab CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Check Windows development prerequisites.")
    doctor.set_defaults(handler=doctor_command)

    scenario = commands.add_parser("scenario", help="Validate versioned Scenario contracts.")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    validate = scenario_commands.add_parser("validate", help="Validate one Scenario YAML file.")
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=scenario_validate)
    return parser


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


if __name__ == "__main__":
    raise SystemExit(main())
