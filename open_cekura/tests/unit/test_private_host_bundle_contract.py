from __future__ import annotations

from scripts.build_private_host_bundle import source_selection


def test_private_host_bundle_contains_reliability_runtime_not_tests() -> None:
    selected = set(source_selection())

    assert "open_cekura/__init__.py" in selected
    assert "open_cekura/api/routes.py" in selected
    assert not any(path.startswith("open_cekura/tests/") for path in selected)
