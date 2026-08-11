"""Deterministic, explainable failure clustering for v0."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from open_cekura.domain.ids import stable_id
from open_cekura.domain.models import FailureCase, FailureCluster


def cluster_failures(
    campaign_id: str,
    failures: Iterable[FailureCase],
    *,
    created_at: datetime,
) -> list[FailureCluster]:
    """Group failures by stable reason code without probabilistic inference."""

    grouped: dict[str, list[str]] = defaultdict(list)
    for failure in failures:
        signature = failure.reason_code.strip().casefold()
        grouped[signature].append(failure.id)

    return [
        FailureCluster(
            schema_version=1,
            id=stable_id("occluster", campaign_id, signature),
            campaign_id=campaign_id,
            signature=signature,
            failure_case_ids=sorted(set(grouped[signature])),
            created_at=created_at,
        )
        for signature in sorted(grouped)
    ]


__all__ = ["cluster_failures"]
