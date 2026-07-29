"""Pure Relay activation-plan contracts with no host or service side effects."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath


ACTIVATION_PLAN_SCHEMA = "agentops.relay.activation-plan.v0"
MAX_SYSTEMD_SHOW_BYTES = 16 * 1024
MAX_IDENTITY_SIZE = 256 * 1024 * 1024
MAX_ROUTE_KEYS = 256
MAX_ENABLEMENT_LINKS = 1
CONTROLLED_STOP_EXEC_STATUS = 15
UNIT_NAME = "agentops-mis-relay.service"
UNIT_PATH = f"/etc/systemd/system/{UNIT_NAME}"
ENABLEMENT_LINK_PATH = (
    f"/etc/systemd/system/multi-user.target.wants/{UNIT_NAME}"
)
CONFIG_PATH = "/etc/agentops-mis-relay/config.json"
STATE_DIRECTORY = "/var/lib/agentops-mis-relay"
RUNTIME_DIRECTORY = "/run/agentops-mis-relay"
SYSTEMCTL_PATHS = frozenset({"/bin/systemctl", "/usr/bin/systemctl"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
RELEASE_ID_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}-[0-9a-f]{12}\Z"
)
VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
INVOCATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")

SYSTEMD_PROPERTIES = (
    "LoadState",
    "UnitFileState",
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainStatus",
    "FragmentPath",
    "NeedDaemonReload",
    "InvocationID",
    "MainPID",
)


class RelayActivationError(Exception):
    """Expected pure-plan failure represented by a non-sensitive identifier."""

    def __init__(self, error_id: str):
        super().__init__(error_id)
        self.error_id = error_id


@dataclass(frozen=True)
class RootIdentity:
    kind: str
    canonical_path: str
    device_id: int
    inode: int
    owner_id: int
    group_id: int
    mode: int


@dataclass(frozen=True)
class FileIdentity:
    kind: str
    canonical_path: str
    device_id: int
    inode: int
    owner_id: int
    group_id: int
    mode: int
    nlink: int
    size: int
    content_sha256: str


@dataclass(frozen=True)
class DirectoryIdentity:
    kind: str
    canonical_path: str
    device_id: int
    inode: int
    owner_id: int
    group_id: int
    mode: int
    nlink: int


@dataclass(frozen=True)
class LinkIdentity:
    kind: str
    canonical_path: str
    target: str
    device_id: int
    inode: int
    owner_id: int
    group_id: int
    nlink: int


@dataclass(frozen=True)
class SystemdSnapshot:
    load_state: str
    unit_file_state: str
    active_state: str
    sub_state: str
    result: str
    exec_main_status: int
    fragment_path: str
    need_daemon_reload: bool
    invocation_id: str
    main_pid: int


@dataclass(frozen=True)
class ActivationPrerequisiteSnapshot:
    root: RootIdentity
    release_id: str
    version_id: str
    release_tree_sha256: str
    unit: FileIdentity
    config: FileIdentity
    certificate: FileIdentity
    private_key: FileIdentity
    route_keys: tuple[FileIdentity, ...]
    state_directory: DirectoryIdentity
    runtime_directory: DirectoryIdentity
    trusted_parent_chain_sha256: str
    service_uid: int
    service_gid: int
    service_group_ids: tuple[int, ...]
    systemctl: FileIdentity
    enablement_links: tuple[LinkIdentity, ...]
    installed_state: str = "installed_valid"
    service_account_ready: bool = True
    config_ready: bool = True
    tls_material_ready: bool = True
    route_keys_ready: bool = True
    recovery_required: bool = False


@dataclass(frozen=True)
class ActivationPlan:
    state: str
    ok: bool
    release_id: str | None = None
    version_id: str | None = None
    plan_sha256: str | None = None
    systemd: SystemdSnapshot | None = None
    daemon_reload: bool = False
    enable: bool = False
    start: bool = False
    _origin: object | None = field(default=None, repr=False, compare=False)
    _projection_sha256: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )


_COMPILED_PLAN_ORIGIN = object()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_host_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or len(value) > 4096
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RelayActivationError("identity_invalid")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RelayActivationError("identity_invalid") from exc
    path = PurePosixPath(value)
    if value != path.as_posix() or any(part in {".", "..", "~"} for part in path.parts):
        raise RelayActivationError("identity_invalid")
    return value


def _canonical_uint(value: int, *, maximum: int = (2**63) - 1) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise RelayActivationError("identity_invalid")
    return value


def _canonical_decimal(value: str, *, maximum: int = (2**31) - 1) -> int:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        raise RelayActivationError("systemd_state_invalid")
    number = int(value)
    if number > maximum:
        raise RelayActivationError("systemd_state_invalid")
    return number


def _validate_root(identity: RootIdentity) -> dict[str, object]:
    if _canonical_host_path(identity.canonical_path) != "/":
        raise RelayActivationError("identity_invalid")
    mode = _canonical_uint(identity.mode, maximum=0o7777)
    if (
        identity.kind != "directory"
        or mode & 0o022
        or identity.owner_id != 0
        or identity.group_id != 0
    ):
        raise RelayActivationError("identity_invalid")
    return {
        "canonical_path": identity.canonical_path,
        "device_id": _canonical_uint(identity.device_id),
        "group_id": _canonical_uint(identity.group_id),
        "inode": _canonical_uint(identity.inode),
        "kind": identity.kind,
        "mode": mode,
        "owner_id": _canonical_uint(identity.owner_id),
    }


def _validate_file(
    identity: FileIdentity,
    *,
    expected_mode: int | None = None,
    expected_owner: int | None = None,
    require_executable: bool = False,
) -> dict[str, object]:
    path = _canonical_host_path(identity.canonical_path)
    mode = _canonical_uint(identity.mode, maximum=0o7777)
    nlink = _canonical_uint(identity.nlink)
    owner = _canonical_uint(identity.owner_id)
    if (
        identity.kind != "regular"
        or mode & 0o022
        or (expected_mode is not None and mode != expected_mode)
        or (expected_owner is not None and owner != expected_owner)
        or (require_executable and not mode & 0o111)
        or nlink != 1
        or not SHA256_PATTERN.fullmatch(identity.content_sha256)
    ):
        raise RelayActivationError("identity_invalid")
    return {
        "canonical_path": path,
        "content_sha256": identity.content_sha256,
        "device_id": _canonical_uint(identity.device_id),
        "group_id": _canonical_uint(identity.group_id),
        "inode": _canonical_uint(identity.inode),
        "kind": identity.kind,
        "mode": mode,
        "nlink": nlink,
        "owner_id": owner,
        "size": _canonical_uint(identity.size, maximum=MAX_IDENTITY_SIZE),
    }


def _validate_directory(
    identity: DirectoryIdentity,
    *,
    expected_path: str,
    expected_owner: int,
    expected_group: int,
) -> dict[str, object]:
    path = _canonical_host_path(identity.canonical_path)
    mode = _canonical_uint(identity.mode, maximum=0o7777)
    nlink = _canonical_uint(identity.nlink)
    if (
        identity.kind != "directory"
        or path != expected_path
        or identity.owner_id != expected_owner
        or identity.group_id != expected_group
        or mode != 0o700
        or nlink < 2
    ):
        raise RelayActivationError("identity_invalid")
    return {
        "canonical_path": path,
        "device_id": _canonical_uint(identity.device_id),
        "group_id": _canonical_uint(identity.group_id),
        "inode": _canonical_uint(identity.inode),
        "kind": identity.kind,
        "mode": mode,
        "nlink": nlink,
        "owner_id": _canonical_uint(identity.owner_id),
    }


def _validate_link(identity: LinkIdentity) -> dict[str, object]:
    path = _canonical_host_path(identity.canonical_path)
    nlink = _canonical_uint(identity.nlink)
    target = identity.target
    if (
        identity.kind != "symlink"
        or not isinstance(target, str)
        or not target
        or len(target) > 4096
        or "\x00" in target
        or any(ord(character) < 32 or ord(character) == 127 for character in target)
        or nlink != 1
        or target != UNIT_PATH
        or path != ENABLEMENT_LINK_PATH
        or identity.owner_id != 0
        or identity.group_id != 0
    ):
        raise RelayActivationError("identity_invalid")
    try:
        target.encode("ascii")
    except UnicodeEncodeError as exc:
        raise RelayActivationError("identity_invalid") from exc
    return {
        "canonical_path": path,
        "device_id": _canonical_uint(identity.device_id),
        "group_id": _canonical_uint(identity.group_id),
        "inode": _canonical_uint(identity.inode),
        "kind": identity.kind,
        "nlink": nlink,
        "owner_id": _canonical_uint(identity.owner_id),
        "target": target,
    }


def parse_systemd_show_bytes(data: bytes) -> SystemdSnapshot:
    if (
        not isinstance(data, bytes)
        or not data
        or len(data) > MAX_SYSTEMD_SHOW_BYTES
        or b"\x00" in data
        or b"\r" in data
    ):
        raise RelayActivationError("systemd_state_invalid")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RelayActivationError("systemd_state_invalid") from exc
    lines = text.splitlines()
    if len(lines) != len(SYSTEMD_PROPERTIES):
        raise RelayActivationError("systemd_state_invalid")
    values: dict[str, str] = {}
    for line in lines:
        if "=" not in line:
            raise RelayActivationError("systemd_state_invalid")
        name, value = line.split("=", 1)
        if name not in SYSTEMD_PROPERTIES or name in values or len(value) > 4096:
            raise RelayActivationError("systemd_state_invalid")
        values[name] = value
    if set(values) != set(SYSTEMD_PROPERTIES):
        raise RelayActivationError("systemd_state_invalid")

    load_state = values["LoadState"]
    unit_file_state = values["UnitFileState"]
    active_state = values["ActiveState"]
    sub_state = values["SubState"]
    result = values["Result"]
    fragment_path = _canonical_host_path(values["FragmentPath"])
    need_reload = values["NeedDaemonReload"]
    invocation_id = values["InvocationID"]
    exec_status = _canonical_decimal(
        values["ExecMainStatus"],
        maximum=255,
    )
    main_pid = _canonical_decimal(values["MainPID"])

    if (
        load_state != "loaded"
        or unit_file_state not in {"enabled", "disabled"}
        or result not in {"", "success"}
        or need_reload not in {"yes", "no"}
    ):
        raise RelayActivationError("systemd_state_invalid")
    if active_state == "active":
        if (
            sub_state != "running"
            or exec_status != 0
            or main_pid == 0
            or not INVOCATION_ID_PATTERN.fullmatch(invocation_id)
        ):
            raise RelayActivationError("systemd_state_invalid")
    elif active_state == "inactive":
        if (
            sub_state != "dead"
            or main_pid != 0
            or (
                exec_status not in {0, CONTROLLED_STOP_EXEC_STATUS}
            )
            or (
                exec_status == CONTROLLED_STOP_EXEC_STATUS
                and result != "success"
            )
            or (
                invocation_id
                and not INVOCATION_ID_PATTERN.fullmatch(invocation_id)
            )
        ):
            raise RelayActivationError("systemd_state_invalid")
    else:
        raise RelayActivationError("systemd_state_invalid")
    return SystemdSnapshot(
        load_state=load_state,
        unit_file_state=unit_file_state,
        active_state=active_state,
        sub_state=sub_state,
        result=result,
        exec_main_status=exec_status,
        fragment_path=fragment_path,
        need_daemon_reload=need_reload == "yes",
        invocation_id=invocation_id,
        main_pid=main_pid,
    )


def _validate_systemd_snapshot(systemd: SystemdSnapshot) -> None:
    if (
        not isinstance(systemd, SystemdSnapshot)
        or systemd.load_state != "loaded"
        or systemd.unit_file_state not in {"enabled", "disabled"}
        or systemd.result not in {"", "success"}
        or type(systemd.exec_main_status) is not int
        or systemd.exec_main_status < 0
        or systemd.exec_main_status > 255
        or type(systemd.need_daemon_reload) is not bool
        or type(systemd.main_pid) is not int
        or systemd.main_pid < 0
        or systemd.main_pid > (2**31) - 1
    ):
        raise RelayActivationError("systemd_state_invalid")
    try:
        _canonical_host_path(systemd.fragment_path)
    except RelayActivationError as exc:
        raise RelayActivationError("systemd_state_invalid") from exc
    if systemd.active_state == "active":
        if (
            systemd.sub_state != "running"
            or systemd.exec_main_status != 0
            or systemd.main_pid == 0
            or not INVOCATION_ID_PATTERN.fullmatch(systemd.invocation_id)
        ):
            raise RelayActivationError("systemd_state_invalid")
    elif systemd.active_state == "inactive":
        if (
            systemd.sub_state != "dead"
            or systemd.main_pid != 0
            or (
                systemd.exec_main_status
                not in {0, CONTROLLED_STOP_EXEC_STATUS}
            )
            or (
                systemd.exec_main_status
                == CONTROLLED_STOP_EXEC_STATUS
                and systemd.result != "success"
            )
            or (
                systemd.invocation_id
                and not INVOCATION_ID_PATTERN.fullmatch(
                    systemd.invocation_id
                )
            )
        ):
            raise RelayActivationError("systemd_state_invalid")
    else:
        raise RelayActivationError("systemd_state_invalid")


def _service_can_read(
    identity: dict[str, object],
    *,
    service_uid: int,
    service_groups: tuple[int, ...],
) -> bool:
    mode = int(identity["mode"])
    if int(identity["owner_id"]) == service_uid:
        return bool(mode & 0o400)
    if int(identity["group_id"]) in service_groups:
        return bool(mode & 0o040)
    return bool(mode & 0o004)


def _invalid_plan(state: str = "invalid") -> ActivationPlan:
    return ActivationPlan(state=state, ok=False)


def _projection_binding(plan: ActivationPlan) -> str:
    if plan.systemd is None:
        raise RelayActivationError("projection_invalid")
    return _sha256(
        _canonical_json(
            {
                "actions": {
                    "daemon_reload": plan.daemon_reload,
                    "enable": plan.enable,
                    "start": plan.start,
                },
                "ok": plan.ok,
                "plan_sha256": plan.plan_sha256,
                "release_id": plan.release_id,
                "state": plan.state,
                "systemd": {
                    "active_state": plan.systemd.active_state,
                    "exec_main_status": plan.systemd.exec_main_status,
                    "fragment_path": plan.systemd.fragment_path,
                    "invocation_id": plan.systemd.invocation_id,
                    "load_state": plan.systemd.load_state,
                    "main_pid": plan.systemd.main_pid,
                    "need_daemon_reload": plan.systemd.need_daemon_reload,
                    "result": plan.systemd.result,
                    "sub_state": plan.systemd.sub_state,
                    "unit_file_state": plan.systemd.unit_file_state,
                },
                "version_id": plan.version_id,
            }
        )
    )


def _seal_compiled_plan(plan: ActivationPlan) -> ActivationPlan:
    origin_bound = replace(plan, _origin=_COMPILED_PLAN_ORIGIN)
    return replace(
        origin_bound,
        _projection_sha256=_projection_binding(origin_bound),
    )


def compile_activation_plan(
    prerequisites: ActivationPrerequisiteSnapshot,
    systemd: SystemdSnapshot,
) -> ActivationPlan:
    if not isinstance(prerequisites, ActivationPrerequisiteSnapshot):
        return _invalid_plan()
    if type(prerequisites.recovery_required) is not bool:
        return _invalid_plan()
    if prerequisites.recovery_required:
        return _invalid_plan("recovery_required")
    try:
        if (
            prerequisites.installed_state != "installed_valid"
            or prerequisites.service_account_ready is not True
            or prerequisites.config_ready is not True
            or prerequisites.tls_material_ready is not True
            or prerequisites.route_keys_ready is not True
            or not RELEASE_ID_PATTERN.fullmatch(prerequisites.release_id)
            or not VERSION_PATTERN.fullmatch(prerequisites.version_id)
            or not prerequisites.release_id.startswith(
                f"{prerequisites.version_id}-"
            )
            or not SHA256_PATTERN.fullmatch(prerequisites.release_tree_sha256)
            or not SHA256_PATTERN.fullmatch(
                prerequisites.trusted_parent_chain_sha256
            )
        ):
            raise RelayActivationError("prerequisite_invalid")
        _validate_systemd_snapshot(systemd)
        root = _validate_root(prerequisites.root)
        service_uid = _canonical_uint(prerequisites.service_uid)
        service_gid = _canonical_uint(prerequisites.service_gid)
        if service_uid == 0 or service_gid == 0:
            raise RelayActivationError("prerequisite_invalid")
        groups = tuple(prerequisites.service_group_ids)
        if (
            not groups
            or len(groups) > 256
            or groups != tuple(sorted(set(groups)))
            or service_gid not in groups
        ):
            raise RelayActivationError("prerequisite_invalid")
        for group in groups:
            _canonical_uint(group)
        unit = _validate_file(
            prerequisites.unit,
            expected_mode=0o644,
            expected_owner=int(root["owner_id"]),
        )
        config = _validate_file(prerequisites.config)
        certificate = _validate_file(prerequisites.certificate)
        private_key = _validate_file(
            prerequisites.private_key,
            expected_mode=0o600,
            expected_owner=service_uid,
        )
        systemctl = _validate_file(
            prerequisites.systemctl,
            expected_owner=int(root["owner_id"]),
            require_executable=True,
        )
        state_directory = _validate_directory(
            prerequisites.state_directory,
            expected_path=STATE_DIRECTORY,
            expected_owner=service_uid,
            expected_group=service_gid,
        )
        runtime_directory = _validate_directory(
            prerequisites.runtime_directory,
            expected_path=RUNTIME_DIRECTORY,
            expected_owner=service_uid,
            expected_group=service_gid,
        )
        if (
            unit["canonical_path"] != UNIT_PATH
            or config["canonical_path"] != CONFIG_PATH
            or systemctl["canonical_path"] not in SYSTEMCTL_PATHS
            or systemd.fragment_path != UNIT_PATH
        ):
            raise RelayActivationError("prerequisite_invalid")
        route_keys = tuple(prerequisites.route_keys)
        if not route_keys or len(route_keys) > MAX_ROUTE_KEYS:
            raise RelayActivationError("prerequisite_invalid")
        route_payload = tuple(
            _validate_file(
                route,
                expected_mode=0o600,
                expected_owner=service_uid,
            )
            for route in route_keys
        )
        route_paths = tuple(str(value["canonical_path"]) for value in route_payload)
        if route_paths != tuple(sorted(set(route_paths))):
            raise RelayActivationError("prerequisite_invalid")
        sensitive_identities = (config, certificate, private_key, *route_payload)
        sensitive_paths = tuple(
            str(value["canonical_path"]) for value in sensitive_identities
        )
        sensitive_inodes = tuple(
            (int(value["device_id"]), int(value["inode"]))
            for value in sensitive_identities
        )
        if (
            len(set(sensitive_paths)) != len(sensitive_paths)
            or len(set(sensitive_inodes)) != len(sensitive_inodes)
            or not _service_can_read(
                config,
                service_uid=service_uid,
                service_groups=groups,
            )
            or not _service_can_read(
                certificate,
                service_uid=service_uid,
                service_groups=groups,
            )
        ):
            raise RelayActivationError("prerequisite_invalid")
        links = tuple(prerequisites.enablement_links)
        if len(links) > MAX_ENABLEMENT_LINKS:
            raise RelayActivationError("prerequisite_invalid")
        link_payload = tuple(_validate_link(link) for link in links)
        link_paths = tuple(str(value["canonical_path"]) for value in link_payload)
        if (
            link_paths != tuple(sorted(set(link_paths)))
            or (
                systemd.unit_file_state == "enabled"
                and not link_payload
            )
            or (
                systemd.unit_file_state == "disabled"
                and link_payload
            )
        ):
            raise RelayActivationError("prerequisite_invalid")
    except (AttributeError, RelayActivationError, TypeError, ValueError):
        return _invalid_plan()

    if systemd.active_state == "active" and systemd.need_daemon_reload:
        return _invalid_plan()
    if systemd.unit_file_state == "enabled" and systemd.active_state == "active":
        return _seal_compiled_plan(
            ActivationPlan(
                state="already_active",
                ok=True,
                release_id=prerequisites.release_id,
                version_id=prerequisites.version_id,
                systemd=systemd,
            )
        )

    requested_enable = systemd.unit_file_state == "disabled"
    requested_start = systemd.active_state == "inactive"
    private_payload = {
        "enablement_links": link_payload,
        "installed_state": prerequisites.installed_state,
        "prerequisites": {
            "certificate": certificate,
            "config": config,
            "private_key": private_key,
            "root": root,
            "route_keys": route_payload,
            "runtime_directory": runtime_directory,
            "service_gid": service_gid,
            "service_group_ids": groups,
            "service_uid": service_uid,
            "state_directory": state_directory,
            "systemctl": systemctl,
            "trusted_parent_chain_sha256": (
                prerequisites.trusted_parent_chain_sha256
            ),
            "unit": unit,
        },
        "release_id": prerequisites.release_id,
        "release_tree_sha256": prerequisites.release_tree_sha256,
        "requested": {
            "daemon_reload": True,
            "enable": requested_enable,
            "start": requested_start,
        },
        "schema_id": ACTIVATION_PLAN_SCHEMA,
        "systemd": {
            "active_state": systemd.active_state,
            "exec_main_status": systemd.exec_main_status,
            "fragment_path": systemd.fragment_path,
            "invocation_id": systemd.invocation_id,
            "load_state": systemd.load_state,
            "main_pid": systemd.main_pid,
            "need_daemon_reload": systemd.need_daemon_reload,
            "result": systemd.result,
            "sub_state": systemd.sub_state,
            "unit_file_state": systemd.unit_file_state,
        },
        "unit_id": UNIT_NAME,
        "version_id": prerequisites.version_id,
    }
    return _seal_compiled_plan(
        ActivationPlan(
            state="plan_ready",
            ok=True,
            release_id=prerequisites.release_id,
            version_id=prerequisites.version_id,
            plan_sha256=_sha256(_canonical_json(private_payload)),
            systemd=systemd,
            daemon_reload=True,
            enable=requested_enable,
            start=requested_start,
        )
    )


def project_activation_plan(plan: ActivationPlan) -> dict[str, object]:
    if (
        not isinstance(plan, ActivationPlan)
        or plan.state
        not in {"plan_ready", "already_active", "recovery_required", "invalid"}
        or plan.ok is not (plan.state in {"plan_ready", "already_active"})
        or (plan.ok and plan._origin is not _COMPILED_PLAN_ORIGIN)
        or (
            plan.ok
            and (
                plan._projection_sha256 is None
                or not SHA256_PATTERN.fullmatch(plan._projection_sha256)
            )
        )
        or type(plan.daemon_reload) is not bool
        or type(plan.enable) is not bool
        or type(plan.start) is not bool
        or (
            plan.state == "plan_ready"
            and (
                plan.daemon_reload is not True
                or not (plan.enable or plan.start)
            )
        )
        or (
            plan.state == "already_active"
            and (
                plan.plan_sha256 is not None
                or plan.daemon_reload
                or plan.enable
                or plan.start
            )
        )
    ):
        return {
            "ok": False,
            "operation_id": "activate",
            "schema_id": ACTIVATION_PLAN_SCHEMA,
            "state": "invalid",
        }
    if plan.ok:
        try:
            if (
                plan.systemd is None
                or plan.release_id is None
                or plan.version_id is None
                or not RELEASE_ID_PATTERN.fullmatch(plan.release_id)
                or not VERSION_PATTERN.fullmatch(plan.version_id)
                or not plan.release_id.startswith(f"{plan.version_id}-")
            ):
                raise RelayActivationError("projection_invalid")
            _validate_systemd_snapshot(plan.systemd)
            if plan._projection_sha256 != _projection_binding(plan):
                raise RelayActivationError("projection_invalid")
            if plan.systemd.fragment_path != UNIT_PATH:
                raise RelayActivationError("projection_invalid")
            expected_enable = plan.systemd.unit_file_state == "disabled"
            expected_start = plan.systemd.active_state == "inactive"
            if plan.state == "already_active":
                if (
                    plan.systemd.unit_file_state != "enabled"
                    or plan.systemd.active_state != "active"
                    or plan.systemd.need_daemon_reload
                ):
                    raise RelayActivationError("projection_invalid")
            elif (
                plan.systemd.active_state == "active"
                and plan.systemd.need_daemon_reload
            ) or plan.enable is not expected_enable or plan.start is not expected_start:
                raise RelayActivationError("projection_invalid")
        except (RelayActivationError, TypeError):
            return {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "invalid",
            }
    output: dict[str, object] = {
        "ok": plan.ok,
        "operation_id": "activate",
        "schema_id": ACTIVATION_PLAN_SCHEMA,
        "state": plan.state,
    }
    if not plan.ok:
        return output
    if plan.systemd is None or plan.release_id is None or plan.version_id is None:
        return {
            "ok": False,
            "operation_id": "activate",
            "schema_id": ACTIVATION_PLAN_SCHEMA,
            "state": "invalid",
        }
    output.update(
        {
            "prerequisites": {
                "config": "ready",
                "route_keys": "ready",
                "service_account": "ready",
                "tls_material": "ready",
            },
            "release_id": plan.release_id,
            "requested": {
                "daemon_reload": plan.daemon_reload,
                "enable": plan.enable,
                "start": plan.start,
            },
            "systemd": {
                "active_state": plan.systemd.active_state,
                "load_state": plan.systemd.load_state,
                "sub_state": plan.systemd.sub_state,
                "unit_file_state": plan.systemd.unit_file_state,
            },
            "unit_id": UNIT_NAME,
            "version_id": plan.version_id,
        }
    )
    if plan.state == "plan_ready":
        if plan.plan_sha256 is None or not SHA256_PATTERN.fullmatch(
            plan.plan_sha256
        ):
            return {
                "ok": False,
                "operation_id": "activate",
                "schema_id": ACTIVATION_PLAN_SCHEMA,
                "state": "invalid",
            }
        output["plan_sha256"] = plan.plan_sha256
    return output
