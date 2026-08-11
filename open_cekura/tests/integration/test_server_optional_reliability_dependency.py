from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_base_server_schema_starts_without_optional_reliability_dependencies(
    tmp_path: Path,
) -> None:
    database = tmp_path / "base server.db"
    script = (
        "from pathlib import Path\n"
        "import server\n"
        f"server.DB_PATH = Path({str(database)!r})\n"
        "server.init_schema()\n"
        "print('BASE_SERVER_READY')\n"
    )

    completed = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        shell=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "BASE_SERVER_READY"
    assert database.is_file()


@pytest.mark.parametrize("missing_module", ["pydantic", "yaml"])
def test_reliability_route_fails_closed_when_optional_dependency_is_missing(
    monkeypatch,
    missing_module: str,
) -> None:
    import server

    class MissingReliabilityAPI:
        @staticmethod
        def handle_get(*_args, **_kwargs):
            raise ModuleNotFoundError(
                f"No module named '{missing_module}'",
                name=missing_module,
            )

    monkeypatch.setattr(server, "reliability_api", MissingReliabilityAPI())
    connection = sqlite3.connect(":memory:")
    try:
        payload, status = server.reliability_api_get(
            connection,
            path="/api/reliability/overview",
            query={},
            workspace_id="local-demo",
        )
    finally:
        connection.close()

    assert status == 503
    assert payload == {
        "schema_version": "open_cekura.reliability_api.v1",
        "provider": "open-cekura",
        "error": "reliability_dependencies_unavailable",
        "message": "Reliability Lab optional dependencies are not installed.",
        "retryable": False,
        "token_omitted": True,
    }


@pytest.mark.parametrize("missing_module", ["pydantic", "yaml"])
def test_schema_initialization_ignores_only_optional_reliability_dependencies(
    monkeypatch,
    missing_module: str,
) -> None:
    import server

    class MissingReliabilityAPI:
        @staticmethod
        def initialize_schema(*_args, **_kwargs):
            raise ModuleNotFoundError(
                f"No module named '{missing_module}'",
                name=missing_module,
            )

    monkeypatch.setattr(server, "reliability_api", MissingReliabilityAPI())
    connection = sqlite3.connect(":memory:")
    try:
        server.initialize_reliability_schema(connection)
    finally:
        connection.close()


def test_base_server_schema_starts_when_pyyaml_alone_is_missing(tmp_path: Path) -> None:
    database = tmp_path / "base without yaml.db"
    script = (
        "import builtins\n"
        "from pathlib import Path\n"
        "import server\n"
        "real_import = builtins.__import__\n"
        "def guarded_import(name, *args, **kwargs):\n"
        "    if name == 'yaml' or name.startswith('yaml.'):\n"
        "        raise ModuleNotFoundError(\"No module named 'yaml'\", name='yaml')\n"
        "    return real_import(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded_import\n"
        f"server.DB_PATH = Path({str(database)!r})\n"
        "server.init_schema()\n"
        "print('BASE_WITHOUT_YAML_READY')\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        shell=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "BASE_WITHOUT_YAML_READY"
    assert database.is_file()


def test_non_optional_import_failures_are_not_hidden(monkeypatch) -> None:
    import server

    class BrokenReliabilityAPI:
        @staticmethod
        def handle_get(*_args, **_kwargs):
            raise ModuleNotFoundError(
                "No module named 'open_cekura.internal_bug'",
                name="open_cekura.internal_bug",
            )

    monkeypatch.setattr(server, "reliability_api", BrokenReliabilityAPI())
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ModuleNotFoundError):
            server.reliability_api_get(
                connection,
                path="/api/reliability/overview",
                query={},
                workspace_id="local-demo",
            )
    finally:
        connection.close()
