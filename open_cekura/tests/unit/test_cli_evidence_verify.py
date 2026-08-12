from __future__ import annotations

import argparse
from pathlib import Path

from open_cekura.cli import main as cli


def test_evidence_verify_parser_accepts_governed_state_options(tmp_path: Path) -> None:
    database = tmp_path / "state with spaces" / "reliability.db"
    artifacts = tmp_path / "evidence with spaces"

    args = cli.build_parser().parse_args(
        [
            "evidence",
            "verify",
            "--campaign",
            "occampaign_governed",
            "--workspace",
            "workspace-governed",
            "--db",
            str(database),
            "--artifacts",
            str(artifacts),
        ]
    )

    assert args.workspace == "workspace-governed"
    assert args.db == database
    assert args.artifacts == artifacts


def test_evidence_verify_handler_forwards_governed_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    database = tmp_path / "state.db"
    artifacts = tmp_path / "evidence"
    received: dict[str, object] = {}

    def verify_campaign_evidence(**kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {"verified": True, "token_omitted": True}

    monkeypatch.setattr(cli, "verify_campaign_evidence", verify_campaign_evidence)
    args = argparse.Namespace(
        campaign="occampaign_governed",
        workspace="workspace-governed",
        db=database,
        artifacts=artifacts,
        strict=True,
    )

    exit_code = cli.evidence_verify(args)

    assert exit_code == 0
    assert received == {
        "campaign_id": "occampaign_governed",
        "workspace_id": "workspace-governed",
        "db_path": database,
        "artifact_root": artifacts,
        "strict": True,
    }
    assert capsys.readouterr().err == ""
