"""Cross-platform paths and private-file handling for AgentOps clients."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def agentops_data_dir() -> Path:
    configured = os.environ.get("AGENTOPS_HOME")
    if configured:
        return Path(configured).expanduser()
    if is_windows():
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "AgentOps MIS"
    return Path("~/.agentops").expanduser()


def agentops_local_data_dir() -> Path:
    configured = os.environ.get("AGENTOPS_LOCAL_HOME")
    if configured:
        return Path(configured).expanduser()
    if is_windows() and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "AgentOps MIS"
    return agentops_data_dir()


def default_config_path() -> Path:
    configured = os.environ.get("AGENTOPS_CONFIG")
    return Path(configured).expanduser() if configured else agentops_data_dir() / "config.json"


def default_worker_runtime_dir() -> Path:
    configured = os.environ.get("AGENTOPS_WORKER_RUNTIME_DIR")
    return Path(configured).expanduser() if configured else agentops_local_data_dir() / "workers"


def default_windows_service_dir() -> Path:
    return agentops_local_data_dir() / "services"


def _windows_acl_sddl(path: Path) -> str:
    if not is_windows():
        return ""
    import ctypes
    from ctypes import wintypes

    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    security_information = owner_security_information | dacl_security_information
    se_file_object = 1
    sddl_revision_1 = 1
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    get_named = advapi32.GetNamedSecurityInfoW
    get_named.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_named.restype = wintypes.DWORD
    convert = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.ULONG),
    ]
    convert.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = get_named(
        str(path),
        se_file_object,
        security_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if code != 0 or not descriptor.value:
        return ""
    rendered = wintypes.LPWSTR()
    rendered_length = wintypes.ULONG()
    try:
        if not convert(
            descriptor,
            sddl_revision_1,
            security_information,
            ctypes.byref(rendered),
            ctypes.byref(rendered_length),
        ):
            return ""
        return rendered.value or ""
    finally:
        if rendered:
            local_free(ctypes.cast(rendered, ctypes.c_void_p))
        local_free(descriptor)


def windows_current_sid() -> str:
    if not is_windows():
        return ""
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_user = 1
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_token_information.restype = wintypes.BOOL
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    convert_sid.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE

    token = wintypes.HANDLE()
    if not open_process_token(get_current_process(), token_query, ctypes.byref(token)):
        return ""
    try:
        required = wintypes.DWORD()
        get_token_information(token, token_user, None, 0, ctypes.byref(required))
        if required.value == 0:
            return ""
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(token, token_user, buffer, required, ctypes.byref(required)):
            return ""
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        rendered = wintypes.LPWSTR()
        if not convert_sid(sid_pointer, ctypes.byref(rendered)):
            return ""
        try:
            sid = rendered.value or ""
            return sid if sid.startswith("S-") else ""
        finally:
            local_free(ctypes.cast(rendered, ctypes.c_void_p))
    finally:
        close_handle(token)


def harden_private_file(path: Path) -> None:
    """Restrict a credential-bearing file to the current Windows user or POSIX owner."""
    if not is_windows():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return
    sid = windows_current_sid()
    if not sid:
        raise OSError("windows_private_acl_failed:sid_unavailable")
    executable = shutil.which("icacls.exe") or shutil.which("icacls") or "icacls.exe"
    common = {
        "capture_output": True,
        "text": True,
        "timeout": 20,
        "check": False,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    remove_inheritance = subprocess.run([executable, str(path), "/inheritance:r"], **common)
    grant_current = subprocess.run([executable, str(path), "/grant:r", f"*{sid}:(F)"], **common)
    commands_ok = remove_inheritance.returncode == 0 and grant_current.returncode == 0
    status = windows_private_file_acl_status(path) if commands_ok else {
        "acceptable": False,
        "acl_command_ok": False,
        "inheritance_removed": remove_inheritance.returncode == 0,
        "grant_applied": grant_current.returncode == 0,
    }
    if not status.get("acceptable"):
        safe_status = {
            key: status.get(key)
            for key in (
                "acl_command_ok", "owner_matches", "protected", "current_full_control",
                "rule_count", "unexpected_rule_count", "inheritance_removed", "grant_applied",
            )
        }
        raise OSError("windows_private_acl_failed:" + json.dumps(safe_status, sort_keys=True))


def windows_private_file_acl_status(path: Path) -> dict[str, object]:
    """Return bounded private-DACL facts without exposing paths or SID values."""
    sddl = _windows_acl_sddl(path)
    if not sddl:
        return {"acceptable": False, "acl_command_ok": False}
    owner_match = re.search(r"O:(.*?)(?=G:|D:|S:|$)", sddl)
    dacl_match = re.search(r"D:(.*?)(?=S:|$)", sddl)
    current_sid = windows_current_sid()
    if not owner_match or not dacl_match or not current_sid:
        return {"acceptable": False, "acl_command_ok": True, "sddl_valid": False}
    owner_sid = owner_match.group(1)
    dacl = dacl_match.group(1)
    dacl_flags = dacl.split("(", 1)[0]
    raw_aces = re.findall(r"\(([^)]*)\)", dacl)
    aces = [ace.split(";") for ace in raw_aces]
    current_trustees = {current_sid}
    if current_sid.endswith("-500"):
        current_trustees.add("LA")
    elif current_sid.endswith("-501"):
        current_trustees.add("LG")
    trusted_sids = {*current_trustees, "SY", "S-1-5-18", "BA", "S-1-5-32-544"}
    current_full_control = any(
        len(ace) == 6
        and ace[0] == "A"
        and "ID" not in ace[1]
        and ace[2] in {"FA", "0x1f01ff"}
        and ace[5] in current_trustees
        for ace in aces
    )
    unexpected_aces = [
        ace for ace in aces
        if len(ace) != 6
        or ace[0] != "A"
        or "ID" in ace[1]
        or ace[5] not in trusted_sids
    ]
    owner_matches = bool(current_sid.startswith("S-") and owner_sid in trusted_sids)
    protected = "P" in dacl_flags
    acceptable = bool(owner_matches and protected and current_full_control and not unexpected_aces)
    return {
        "acceptable": acceptable,
        "acl_command_ok": True,
        "sddl_valid": True,
        "owner_matches": owner_matches,
        "protected": protected,
        "current_full_control": current_full_control,
        "rule_count": len(aces),
        "unexpected_rule_count": len(unexpected_aces),
    }


def windows_private_file_is_acceptable(path: Path) -> bool:
    """Verify a protected DACL limited to the current user and OS administrators."""
    return windows_private_file_acl_status(path).get("acceptable") is True
