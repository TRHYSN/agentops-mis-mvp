from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def assert_succeeded(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"subprocess failed with {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_generic_cli_help_does_not_import_private_host() -> None:
    result = run_isolated(
        """
        import importlib.abc
        import sys

        blocked = {"agentops_mis_cli.host"}

        class BlockPrivateHost(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in blocked:
                    raise RuntimeError(f"forbidden eager import: {fullname}")
                return None

        sys.meta_path.insert(0, BlockPrivateHost())

        from agentops_mis_cli import cli

        try:
            exit_code = cli.main(["--help"])
        except SystemExit as exc:
            exit_code = exc.code

        assert exit_code in (None, 0)
        assert blocked.isdisjoint(sys.modules)
        """
    )

    assert_succeeded(result)


def test_server_import_does_not_import_posix_private_host_modules() -> None:
    result = run_isolated(
        """
        import importlib.abc
        import sys

        blocked = {
            "agentops_mis_cli.host",
            "agentops_mis_cli.relay_control",
            "agentops_mis_cli.relay_restart",
        }

        class BlockPrivateHost(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in blocked:
                    raise RuntimeError(f"forbidden eager import: {fullname}")
                return None

        sys.meta_path.insert(0, BlockPrivateHost())

        import server

        assert blocked.isdisjoint(sys.modules)
        """
    )

    assert_succeeded(result)


def test_private_host_command_fails_closed_on_windows_without_posix_import() -> None:
    result = run_isolated(
        """
        import importlib.abc
        import sys

        blocked = {"agentops_mis_cli.host"}

        class BlockPrivateHost(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in blocked:
                    raise RuntimeError(f"forbidden eager import: {fullname}")
                return None

        sys.meta_path.insert(0, BlockPrivateHost())

        from agentops_mis_cli import cli

        original_platform = sys.platform
        sys.platform = "win32"
        try:
            exit_code = cli.main(["host", "status"])
        finally:
            sys.platform = original_platform

        assert exit_code == 2
        assert blocked.isdisjoint(sys.modules)
        """
    )

    assert_succeeded(result)
