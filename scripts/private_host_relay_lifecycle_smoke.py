#!/usr/bin/env python3
"""Verify that the Private Host owns an explicitly enabled Relay connector."""
from __future__ import annotations

import json
import os
import shutil
import shlex
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.relay_connector_service_smoke import (  # noqa: E402
    ROUTE,
    service_command,
    start_relay,
    wait_status,
    write_private_json,
)
from scripts.run_local_stack import (  # noqa: E402
    PROCESS_KILL_GRACE_SECONDS,
    PROCESS_SHUTDOWN_GRACE_SECONDS,
    projected_environment,
)
from scripts.relay_tls_authenticated_tunnel_smoke import (  # noqa: E402
    HOST_HOSTNAME,
    RELAY_HOSTNAME,
    generate_certificate,
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def loopback_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
            stream.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def run_host(env: dict[str, str], *args: str, expected: tuple[int, ...] = (0,)) -> tuple[int, dict]:
    process = subprocess.run(
        [sys.executable, "-m", "agentops_mis_cli.cli", "host", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=70,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        payload = {}
    if process.returncode not in expected:
        raise RuntimeError(f"host command exited {process.returncode}")
    return process.returncode, payload


def read_relay_projection(env: dict[str, str]) -> dict:
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from agentops_mis_cli.host import relay_connector_projection; "
                "print(json.dumps(relay_connector_projection(), sort_keys=True))"
            ),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    try:
        payload = json.loads(process.stdout)
    except ValueError:
        return {}
    return payload if process.returncode == 0 and isinstance(payload, dict) else {}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def relay_children(parent_pid: int) -> list[int]:
    process = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    children: list[int] = []
    if process.returncode != 0:
        return children
    for raw in process.stdout.splitlines():
        columns = raw.strip().split(None, 2)
        if len(columns) != 3:
            continue
        try:
            pid = int(columns[0])
            parent = int(columns[1])
        except ValueError:
            continue
        if parent == parent_pid and "-m agentops_mis_cli.relay_connector_service" in columns[2]:
            children.append(pid)
    return children


def process_environment_keys(pid: int) -> set[str]:
    process = subprocess.run(
        ["/bin/ps", "eww", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if process.returncode != 0:
        return set()
    return {
        token.split("=", 1)[0]
        for token in process.stdout.split()
        if "=" in token and token.split("=", 1)[0].replace("_", "").isalnum()
    }


def wait_for(predicate, timeout: float = 12.0):
    deadline = time.monotonic() + timeout
    value = predicate()
    while time.monotonic() < deadline and not value:
        time.sleep(0.05)
        value = predicate()
    return value


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print(json.dumps({"ok": False, "error": "openssl_unavailable"}, sort_keys=True))
        return 1

    failures: list[str] = []
    disabled_no_process = False
    enabled_owned = False
    restart_recycled = False
    stop_reaped = False
    invalid_failed_closed = False
    tailscale_unchanged = False
    relay_environment_minimal = False
    foreign_connector_preserved = False
    epoch_startup_failed_closed = False
    prepared_material_preflight = False
    active_relay_preflight_rejected = False
    managed_runtime_projection = False
    bounded_shutdown = PROCESS_SHUTDOWN_GRACE_SECONDS + PROCESS_KILL_GRACE_SECONDS < 10
    if not bounded_shutdown:
        failures.append("Stack cleanup bound exceeds the Host stop grace period")

    with tempfile.TemporaryDirectory(prefix="agentops-host-relay-lifecycle-") as temporary:
        temporary_path = Path(temporary)
        host_home = temporary_path / "host"
        relay_home = host_home / "relay"
        ui_dist = temporary_path / "ui"
        ui_dist.mkdir(mode=0o700)
        (ui_dist / "index.html").write_text("<!doctype html><div id='root'>HOST_RELAY</div>\n", encoding="utf-8")

        fake_bin = temporary_path / "bin"
        fake_bin.mkdir(mode=0o700)
        tailscale_log = temporary_path / "tailscale.log"
        fake_tailscale = fake_bin / "tailscale"
        fake_tailscale.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> "
            + shlex.quote(str(tailscale_log))
            + "\nexit 1\n",
            encoding="utf-8",
        )
        fake_tailscale.chmod(0o700)

        env = os.environ.copy()
        env.update(
            {
                "AGENTOPS_HOST_HOME": str(host_home),
                "AGENTOPS_API_KEY": "synthetic-api-key",
                "AGENTOPS_ADMIN_KEY": "synthetic-admin-key",
                "AGENTOPS_OWNER_SETUP_CODE": "synthetic-owner-code",
                "AGENTOPS_HUMAN_SESSION": "synthetic-human-session",
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            }
        )
        backend_port = free_port()
        host_tls_port = free_port()
        unused_relay_port = free_port()
        foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            run_host(
                env,
                "init",
                "--port",
                str(backend_port),
                "--workspace-id",
                "relay-lifecycle-smoke",
                "--ui-dist",
                str(ui_dist),
            )

            run_host(env, "start", "--no-workers")
            disabled_pid = int(read_json(host_home / "run" / "host.pid.json").get("pid") or 0)
            disabled_no_process = bool(
                disabled_pid
                and not relay_children(disabled_pid)
                and not (relay_home / "status.json").exists()
            )
            if not disabled_no_process:
                failures.append("disabled Host created Relay runtime state or a connector process")
            run_host(env, "stop")

            write_private_json(relay_home / "config.json", {"enabled": True, "schema_version": 1})
            invalid_code, invalid_payload = run_host(env, "start", "--no-workers", expected=(1, 2))
            invalid_failed_closed = bool(
                invalid_code in {1, 2}
                and invalid_payload.get("ok") is False
                and not (host_home / "run" / "host.pid.json").exists()
            )
            if not invalid_failed_closed:
                failures.append("invalid Relay config did not fail Host startup closed")

            relay_certificate, relay_key = generate_certificate(
                openssl,
                relay_home,
                prefix="relay",
                hostname=RELAY_HOSTNAME,
            )
            host_certificate, host_key = generate_certificate(
                openssl,
                relay_home,
                prefix="host",
                hostname=HOST_HOSTNAME,
            )
            host_key.chmod(0o600)
            relay_server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            relay_server_context.minimum_version = ssl.TLSVersion.TLSv1_2
            relay_server_context.load_cert_chain(str(relay_certificate), str(relay_key))
            tunnel_key = os.urandom(32)
            relay_fixture, relay_tls_listener, _browser_address = start_relay(
                browser_port=free_port(),
                connector_port=unused_relay_port,
                context=relay_server_context,
                tunnel_key=tunnel_key,
            )
            relay_config = {
                "enabled": True,
                "host_certificate_path": str(host_certificate),
                "host_http_port": backend_port,
                "host_private_key_path": str(host_key),
                "host_server_hostname": HOST_HOSTNAME,
                "host_tls_listen_port": host_tls_port,
                "relay_ca_path": str(relay_certificate),
                "relay_host": "127.0.0.1",
                "relay_port": unused_relay_port,
                "relay_server_hostname": RELAY_HOSTNAME,
                "route": ROUTE,
                "schema_version": 1,
            }
            write_private_json(
                relay_home / "secrets.json",
                {"schema_version": 1, "tunnel_key_hex": tunnel_key.hex()},
            )
            write_private_json(relay_home / "prepared.json", relay_config)
            write_private_json(relay_home / "config.json", {"enabled": False, "schema_version": 1})
            preflight_code, preflight = run_host(env, "relay-preflight")
            prepared_material_preflight = bool(
                preflight_code == 0
                and preflight.get("ok") is True
                and preflight.get("state") == "prepared"
                and preflight.get("exact_material_validated") is True
                and preflight.get("active_relay_enabled") is False
                and preflight.get("network_used") is False
                and relay_tls_listener.accepted_versions() == []
                and loopback_port_available(host_tls_port)
                and json.loads((relay_home / "config.json").read_text(encoding="utf-8"))
                == {"enabled": False, "schema_version": 1}
                and not (relay_home / "status.json").exists()
                and not (relay_home / "epoch.json").exists()
            )
            if not prepared_material_preflight:
                failures.append("prepared Relay material preflight mutated or used the network")
            write_private_json(relay_home / "config.json", relay_config)
            active_preflight_code, active_preflight = run_host(
                env,
                "relay-preflight",
                expected=(1,),
            )
            active_relay_preflight_rejected = bool(
                active_preflight_code == 1
                and active_preflight.get("ok") is False
                and active_preflight.get("error") == "active_relay_not_disabled"
                and relay_tls_listener.accepted_versions() == []
            )
            if not active_relay_preflight_rejected:
                failures.append("preflight accepted an already-enabled active Relay config")

            foreign_epoch = relay_home / "foreign-epoch.json"
            foreign_connector = subprocess.Popen(
                service_command(
                    relay_home / "config.json",
                    relay_home / "secrets.json",
                    foreign_epoch,
                    relay_home / "status.json",
                ),
                cwd=ROOT,
                env=projected_environment(env),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                foreign_ready = wait_status(
                    relay_home / "status.json",
                    lambda payload: payload.get("state") == "connected",
                )
                foreign_status_path = relay_home / "status.json"
                foreign_status_metadata = foreign_status_path.stat()
                foreign_status_bytes = foreign_status_path.read_bytes()
                conflict_code, conflict_payload = run_host(
                    env,
                    "start",
                    "--no-workers",
                    expected=(1,),
                )
                foreign_connector_preserved = bool(
                    foreign_ready.get("host_tls_ready") is True
                    and conflict_code == 1
                    and conflict_payload.get("ok") is False
                    and foreign_connector.poll() is None
                    and not (host_home / "run" / "host.pid.json").exists()
                    and foreign_status_path.stat().st_ino == foreign_status_metadata.st_ino
                    and foreign_status_path.read_bytes() == foreign_status_bytes
                    and read_json(foreign_status_path).get("host_lifecycle_integrated") is False
                )
                if not foreign_connector_preserved:
                    failures.append("Host adopted or terminated a separately owned Relay connector")
            finally:
                if foreign_connector.poll() is None:
                    foreign_connector.send_signal(signal.SIGTERM)
                    foreign_connector.wait(timeout=10)
                relay_fixture.stop()
                relay_tls_listener.close()

            corrupt_epoch = relay_home / "epoch.json"
            corrupt_epoch.write_text("{not-json\n", encoding="utf-8")
            corrupt_epoch.chmod(0o600)
            epoch_code, epoch_payload = run_host(env, "start", "--no-workers", expected=(1,))
            epoch_status = read_json(relay_home / "status.json")
            epoch_startup_failed_closed = bool(
                epoch_code == 1
                and epoch_payload.get("ok") is False
                and epoch_status.get("state") == "failed"
                and epoch_status.get("current_epoch") is None
                and not (host_home / "run" / "host.pid.json").exists()
            )
            if not epoch_startup_failed_closed:
                failures.append("Host accepted Relay readiness before durable epoch initialization")
            corrupt_epoch.unlink(missing_ok=True)

            run_host(env, "start", "--no-workers")
            first_stack_pid = int(read_json(host_home / "run" / "host.pid.json").get("pid") or 0)
            first_children = wait_for(lambda: relay_children(first_stack_pid)) or []
            first_status = wait_for(
                lambda: (
                    payload
                    if (payload := read_json(relay_home / "status.json")).get("host_tls_ready") is True
                    else {}
                )
            ) or {}
            enabled_owned = bool(
                len(first_children) == 1
                and first_status.get("enabled") is True
                and first_status.get("host_lifecycle_integrated") is True
                and int(first_status.get("current_epoch") or 0) > 0
                and first_status.get("state") in {"connecting", "connected", "backoff"}
            )
            if not enabled_owned:
                failures.append("enabled Relay connector was not owned by the Host stack")

            relay_projection = read_relay_projection(env)
            managed_runtime_projection = bool(
                relay_projection.get("enabled") is True
                and relay_projection.get("host_lifecycle_integrated") is True
                and relay_projection.get("runtime_ready") is True
                and relay_projection.get("remote_ready") is False
                and relay_projection.get("ready") is False
                and relay_projection.get("state") in {"connecting", "connected", "backoff"}
            )
            if not managed_runtime_projection:
                failures.append("Host status did not verify the managed Relay runtime conservatively")

            first_connector_pid = first_children[0] if first_children else 0
            relay_environment_keys = process_environment_keys(first_connector_pid)
            relay_environment_minimal = bool(
                {"HOME", "PATH"}.issubset(relay_environment_keys)
                and not any(key.startswith("AGENTOPS_") for key in relay_environment_keys)
            )
            if not relay_environment_minimal:
                failures.append("Relay connector inherited Host authority environment")

            run_host(env, "restart", "--no-workers")
            second_stack_pid = int(read_json(host_home / "run" / "host.pid.json").get("pid") or 0)
            second_children = wait_for(lambda: relay_children(second_stack_pid)) or []
            restart_recycled = bool(
                second_stack_pid
                and second_stack_pid != first_stack_pid
                and len(second_children) == 1
                and second_children[0] != first_connector_pid
                and not process_alive(first_connector_pid)
            )
            if not restart_recycled:
                failures.append("Host restart did not recycle the owned Relay connector")

            second_connector_pid = second_children[0] if second_children else 0
            run_host(env, "stop")
            stop_reaped = bool(
                not process_alive(second_stack_pid)
                and not process_alive(second_connector_pid)
                and process_alive(foreign.pid)
            )
            if not stop_reaped:
                failures.append("Host stop did not reap only its owned Relay process tree")

            tailscale_unchanged = not tailscale_log.exists()
            if not tailscale_unchanged:
                failures.append("Relay lifecycle changed or queried Tailscale")
            if stat.S_IMODE((relay_home / "config.json").stat().st_mode) != 0o600:
                failures.append("Relay config permissions changed")
            if stat.S_IMODE((relay_home / "secrets.json").stat().st_mode) != 0o600:
                failures.append("Relay secrets permissions changed")
        finally:
            try:
                run_host(env, "stop", expected=(0, 2))
            except Exception:
                pass
            if foreign.poll() is None:
                foreign.send_signal(signal.SIGTERM)
                foreign.wait(timeout=5)

    result = {
        "active_relay_preflight_rejected": active_relay_preflight_rejected,
        "deployed_relay": False,
        "bounded_shutdown": bounded_shutdown,
        "disabled_no_connector_process": disabled_no_process,
        "enabled_connector_host_owned": enabled_owned,
        "epoch_startup_failed_closed": epoch_startup_failed_closed,
        "failures": failures,
        "foreign_connector_preserved": foreign_connector_preserved,
        "invalid_config_failed_closed": invalid_failed_closed,
        "managed_runtime_projection": managed_runtime_projection,
        "ok": not failures,
        "operation": "private_host_relay_lifecycle_smoke",
        "prepared_material_preflight": prepared_material_preflight,
        "relay_environment_minimal": relay_environment_minimal,
        "restart_recycled_connector": restart_recycled,
        "stop_reaped_owned_processes": stop_reaped,
        "tailscale_changed": not tailscale_unchanged,
        "token_omitted": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
