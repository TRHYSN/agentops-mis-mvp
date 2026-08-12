from __future__ import annotations

import json

from open_cekura.cli import main as cli
from open_cekura.release_gate.policy import (
    IncomparableCampaignsError,
    ReleaseGateError,
)


def test_compare_reports_incomparable_contract_without_generic_error(
    monkeypatch, capsys
) -> None:
    def reject_comparison(**_kwargs):
        raise IncomparableCampaignsError(
            "candidate tool contract differs from baseline"
        )

    monkeypatch.setattr(cli, "compare_campaigns", reject_comparison)

    exit_code = cli.main(
        ["campaign", "compare", "--baseline", "base_1", "--candidate", "candidate_1"]
    )

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error"] == "incomparable_campaigns"
    assert payload["comparison_status"] == "INCOMPARABLE"
    assert payload["operation"] == "campaign_compare"


def test_standalone_gate_error_is_not_reported_as_incomparable(
    monkeypatch, capsys
) -> None:
    def reject_gate(**_kwargs):
        raise ReleaseGateError("unsupported release gate policy version")

    monkeypatch.setattr(cli, "evaluate_campaign_gate", reject_gate)

    exit_code = cli.main(["gate", "evaluate", "--campaign", "campaign_1"])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 2
    assert payload["error"] == "release_gate_error"
    assert "comparison_status" not in payload
    assert payload["operation"] == "gate_evaluate"
