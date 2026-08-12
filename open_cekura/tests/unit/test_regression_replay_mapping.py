from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def mapping_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "ocreplaymap_source_target_regression",
        "created_at": NOW,
        "source_campaign_id": "occampaign_source",
        "target_campaign_id": "occampaign_target",
        "regression_case_id": "ocregression_confirmation",
        "source_run_id": "ocrun_source",
        "source_evaluation_result_id": "evr_confirmation",
        "evaluator_id": "confirmation_before_mutation.v1",
        "source_scenario_id": "appointment.basic",
        "replay_scenario_id": "appointment.basic",
        "replay_run_id": "ocrun_replay",
        "source_snapshot_sha256": SHA,
        "source_scenario_sha256": "b" * 64,
        "replay_scenario_sha256": "b" * 64,
        "mis_memory_id": "mem_a",
    }


def test_regression_replay_mapping_is_a_strict_serializable_domain_object() -> None:
    models = import_module("open_cekura.domain.models")
    assert hasattr(models, "RegressionReplayMapping")

    mapping = models.RegressionReplayMapping.model_validate(mapping_payload())

    assert mapping.model_dump(mode="json")["source_campaign_id"] == (
        "occampaign_source"
    )
    assert b'"source_snapshot_sha256":"' + SHA.encode("ascii") in (
        mapping.canonical_json_bytes()
    )


def test_regression_replay_mapping_rejects_unknown_fields_and_invalid_hashes() -> None:
    models = import_module("open_cekura.domain.models")
    mapping_type = models.RegressionReplayMapping

    with pytest.raises(ValidationError):
        mapping_type.model_validate({**mapping_payload(), "unknown": True})
    with pytest.raises(ValidationError):
        mapping_type.model_validate(
            {**mapping_payload(), "source_snapshot_sha256": "not-a-sha"}
        )
