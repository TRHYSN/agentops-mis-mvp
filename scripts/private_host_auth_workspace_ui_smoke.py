#!/usr/bin/env python3
"""Statically verify the Private Host auth workspace shell contract."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "ui" / "start-building-app" / "index.html"
UI_COMPONENTS = ROOT / "ui" / "start-building-app" / "src" / "app" / "components"
AUTH_GATE = UI_COMPONENTS / "auth" / "AuthGate.tsx"
APP_SHELL = UI_COMPONENTS / "layout" / "AppShell.tsx"
SIDEBAR = UI_COMPONENTS / "layout" / "Sidebar.tsx"
TOPBAR = UI_COMPONENTS / "layout" / "Topbar.tsx"
ACCOUNT_SECURITY = UI_COMPONENTS / "pages" / "AccountSecurity.tsx"
WORKSPACE_SETTINGS = UI_COMPONENTS / "shared" / "WorkspaceSettings.tsx"
PREFERENCES = ROOT / "ui" / "start-building-app" / "src" / "app" / "context" / "PreferencesContext.tsx"
LIVE_API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"
FILES = (INDEX_HTML, AUTH_GATE, APP_SHELL, SIDEBAR, TOPBAR, ACCOUNT_SECURITY, WORKSPACE_SETTINGS, PREFERENCES, LIVE_API)


def load_source(path: Path, failures: list[str]) -> str:
    if not path.is_file():
        failures.append(f"file_exists:{path.name}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        failures.append(f"file_readable:{path.name}")
        return ""


def record(checks: dict[str, bool], name: str, condition: bool) -> None:
    checks[name] = bool(condition)


def slice_between(source: str, start: str, end: str) -> str:
    start_index = source.find(start)
    if start_index < 0:
        return ""
    end_index = source.find(end, start_index + len(start))
    if end_index < 0:
        return ""
    return source[start_index:end_index]


def main() -> int:
    failures: list[str] = []
    sources = {path: load_source(path, failures) for path in FILES}
    index_html = sources[INDEX_HTML]
    auth_gate = sources[AUTH_GATE]
    app_shell = sources[APP_SHELL]
    sidebar = sources[SIDEBAR]
    topbar = sources[TOPBAR]
    account_security = sources[ACCOUNT_SECURITY]
    workspace_settings = sources[WORKSPACE_SETTINGS]
    preferences = sources[PREFERENCES]
    live_api = sources[LIVE_API]
    checks: dict[str, bool] = {}

    record(
        checks,
        "browser_metadata_uses_product_identity",
        "<title>AgentOps MIS</title>" in index_html
        and "Local-first workspace for AI worker tasks" in index_html
        and "Start Building App" not in index_html,
    )
    record(checks, "auth_gate_reuses_locked_app_shell", '<AppShell locked lockLabel={lockLabel}>' in auth_gate)
    record(checks, "human_auth_workspace_gate_present", 'testId="human-auth-workspace-gate"' in auth_gate)
    record(checks, "auth_gate_uses_workspace_settings_layout", 'testId="human-auth-settings-layout"' in auth_gate)
    record(checks, "auth_gate_uses_workspace_access_panel", 'data-testid="human-auth-access-panel"' in auth_gate)
    record(checks, "auth_gate_uses_workspace_form", 'data-testid="human-auth-workspace-form"' in auth_gate)
    record(checks, "auth_gate_uses_compact_host_boundary", 'data-testid="human-auth-host-boundary"' in auth_gate)
    record(checks, "auth_gate_has_no_standalone_status_panel", 'data-testid="human-auth-host-boundary-panel"' not in auth_gate)
    record(
        checks,
        "auth_gate_has_no_standalone_card_shell",
        'className="overflow-hidden rounded-lg"' not in auth_gate,
    )
    record(
        checks,
        "auth_gate_uses_existing_settings_grid",
        'lg:grid-cols-[220px_minmax(0,1fr)]' in workspace_settings,
    )
    record(
        checks,
        "auth_and_account_share_workspace_settings_components",
        "WorkspaceSettingsPage" in auth_gate
        and "WorkspaceSettingsSection" in auth_gate
        and "WorkspaceSettingsPage" in account_security
        and "WorkspaceSettingsSection" in account_security,
    )
    record(
        checks,
        "account_route_is_visible_in_existing_sidebar",
        'path: "/workspace/account"' in sidebar
        and 'account: "Account and access"' in sidebar
        and 'account: "账户与访问"' in sidebar,
    )
    record(
        checks,
        "locked_sidebar_marks_account_as_current",
        'item.path === "/workspace/account"' in sidebar
        and 'aria-current={isActive ? "page" : undefined}' in sidebar,
    )
    locked_sidebar_footer = slice_between(sidebar, "{locked ? (\n          <>", "\n        ) : (")
    record(
        checks,
        "locked_sidebar_omits_demo_identity",
        "copy.productMode" in locked_sidebar_footer
        and "copy.lockedNavigation" in locked_sidebar_footer
        and "jiwu@agentops.dev" not in locked_sidebar_footer,
    )
    record(
        checks,
        "authenticated_shell_uses_real_account_identity",
        "useHumanAuth" in sidebar
        and "user?.workspace_id" in sidebar
        and "user?.display_name" in sidebar
        and "user?.username" in sidebar,
    )
    record(
        checks,
        "mobile_topbar_keeps_product_identity",
        "md:hidden" in topbar and "AgentOps MIS" in topbar and "<Zap" in topbar,
    )
    record(
        checks,
        "fresh_install_defaults_to_enterprise_theme",
        'if (stored === "dark" || stored === "ops") return "ops";' in preferences
        and 'if (stored === "workforce") return "workforce";' in preferences
        and 'return "enterprise";' in preferences,
    )
    record(
        checks,
        "locked_shell_uses_workspace_content_spacing",
        'locked ? "app-main-locked" : ""' in app_shell
        and "flex-1 overflow-y-auto p-4 lg:p-5" in app_shell,
    )
    record(
        checks,
        "auth_form_uses_compact_workspace_rows",
        'grid gap-1.5 py-3 text-xs sm:grid-cols-[160px_minmax(0,1fr)]' in auth_gate
        and 'background: "var(--mis-surface)"' in auth_gate,
    )
    record(
        checks,
        "auth_gate_uses_single_lock_status",
        "status={(" not in auth_gate
        and "<Topbar locked={locked} lockLabel={lockLabel} />" in app_shell,
    )
    record(
        checks,
        "administrator_bootstrap_copy_bilingual",
        all(
            marker in auth_gate
            for marker in (
                'zh: isBootstrap ? "\u8bbe\u7f6e\u7ba1\u7406\u5458\u8d26\u6237"',
                'en: isBootstrap ? "Set up administrator"',
                'zh: isBootstrap ? "\u521b\u5efa\u7ba1\u7406\u5458\u5e76\u8fdb\u5165"',
                'en: isBootstrap ? "Create administrator"',
            )
        ),
    )
    record(
        checks,
        "login_copy_bilingual",
        all(
            marker in auth_gate
            for marker in (
                'isPairing ? "\u8bbe\u7f6e\u6210\u5458\u8d26\u6237" : "\u767b\u5f55"',
                'isPairing ? "Set up member account" : "Sign in"',
                '"\u8f93\u5165\u8d26\u6237\u4fe1\u606f\u7ee7\u7eed\u3002"',
                '"Enter your account details to continue."',
            )
        ),
    )
    record(
        checks,
        "forgot_password_action_bilingual",
        'status?.password_recovery_available' in auth_gate
        and "onClick={() => void beginRecovery()}" in auth_gate
        and 'zh: "\u5fd8\u8bb0\u5bc6\u7801", en: "Forgot password"' in auth_gate
        and "<KeyRound" in auth_gate,
    )
    record(
        checks,
        "recovery_page_is_local_only_and_revokes_other_sessions",
        all(
            marker in auth_gate
            for marker in (
                'isRecovery ? "\u4ec5\u53ef\u5728\u5b89\u88c5 AgentOps MIS \u7684\u4e3b\u673a\u4e0a\u5b8c\u6210"',
                'isRecovery ? "Complete recovery on the AgentOps MIS host"',
                '"\u8bbe\u7f6e\u65b0\u5bc6\u7801\u540e\uff0c\u5176\u4ed6\u5df2\u767b\u5f55\u8bbe\u5907\u4f1a\u81ea\u52a8\u9000\u51fa\u3002"',
                '"Other signed-in devices will be signed out after the password changes."',
            )
        ),
    )
    record(
        checks,
        "recovery_can_return_to_login",
        "onClick={returnToLogin}" in auth_gate
        and 'zh: "\u8fd4\u56de\u767b\u5f55", en: "Back to sign in"' in auth_gate
        and "<ArrowLeft" in auth_gate,
    )

    confirm_password = slice_between(
        auth_gate,
        'label={pick(locale, { zh: "\u786e\u8ba4\u5bc6\u7801", en: "Confirm password" })}',
        "                    )}",
    )
    record(
        checks,
        "confirm_password_copy_bilingual",
        'zh: "\u786e\u8ba4\u5bc6\u7801", en: "Confirm password"' in confirm_password,
    )
    record(checks, "confirm_password_min_length_12", "minLength={12}" in confirm_password)
    record(
        checks,
        "bootstrap_and_recovery_password_min_length_12",
        "minLength={isBootstrap || isRecovery || isPairing ? 12 : undefined}" in auth_gate,
    )
    record(
        checks,
        "bootstrap_and_recovery_password_live_validation",
        all(
            marker in auth_gate
            for marker in (
                "const passwordLength = Array.from(password).length;",
                "const passwordReady = passwordLength >= 12;",
                "const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword;",
                "const bootstrapFormReady = Boolean(setupCode.trim()) && usernameReady && passwordReady && passwordsMatch;",
                "const recoveryFormReady = Boolean(recoveryAuthority) && usernameReady && passwordReady && passwordsMatch;",
                "const pairingFormReady = Boolean(pairingSecret) && usernameReady && passwordReady && passwordsMatch;",
                "disabled={submitting || (isBootstrap && !bootstrapFormReady) || (isRecovery && !recoveryFormReady) || (isPairing && !pairingFormReady)}",
            )
        ),
    )
    record(
        checks,
        "passphrase_guidance_bilingual",
        all(
            marker in auth_gate
            for marker in (
                'zh: "至少 12 个字符，不要求大小写或符号组合"',
                'en: "At least 12 characters; no composition rules"',
                'zh: "长度已满足", en: "Length requirement met"',
                'zh: "两次输入一致", en: "Passwords match"',
                'zh: "两次输入不一致", en: "Passwords do not match"',
            )
        ),
    )
    record(
        checks,
        "password_policy_has_no_composition_rules",
        not any(
            marker in auth_gate.lower()
            for marker in (
                "uppercase letter",
                "special character",
                "at least one number",
                "大写字母",
                "特殊字符",
                "至少一个数字",
            )
        ),
    )
    record(
        checks,
        "password_visibility_controls_are_accessible_icons",
        "Eye," in auth_gate
        and "EyeOff," in auth_gate
        and "aria-label={revealControl.label}" in auth_gate
        and "aria-pressed={revealControl.visible}" in auth_gate
        and 'type="button"' in auth_gate
        and "togglePasswordLabel" in auth_gate
        and "toggleConfirmPasswordLabel" in auth_gate,
    )
    record(
        checks,
        "password_visibility_resets_across_auth_boundaries",
        auth_gate.count("setShowPassword(false);") >= 3
        and auth_gate.count("setShowConfirmPassword(false);") >= 3,
    )
    record(
        checks,
        "auth_fields_keep_explicit_label_binding",
        "const inputId = useId();" in auth_gate
        and "<label htmlFor={inputId}" in auth_gate
        and "id={inputId}" in auth_gate,
    )
    record(
        checks,
        "username_html_pattern_is_browser_valid",
        '"[a-z0-9][a-z0-9._\\\\-]{2,63}"' in auth_gate,
    )
    record(
        checks,
        "normal_setup_omits_internal_authority_copy",
        not any(
            marker in auth_gate
            for marker in (
                "主机设置码",
                "安装授权",
                "Host setup code",
                "Installation authorization",
            )
        ),
    )
    record(
        checks,
        "manual_setup_authority_is_advanced_only",
        "{isBootstrap && !hasInstallerHandoff && (" in auth_gate
        and 'data-testid="human-auth-advanced-setup"' in auth_gate
        and "<details" in auth_gate
        and 'zh: "无法继续？使用手动初始化", en: "Can\'t continue? Use manual setup"' in auth_gate
        and 'zh: "初始化密钥", en: "Initialization key"' in auth_gate
        and 'autoComplete="one-time-code"' in auth_gate,
    )
    installer_handoff = slice_between(
        auth_gate,
        "{hasInstallerHandoff && (",
        "                    )}",
    )
    record(
        checks,
        "installer_handoff_is_screen_reader_only",
        'data-testid="owner-setup-handoff-ready"' in installer_handoff
        and 'className="sr-only"' in installer_handoff
        and 'zh: "初始化已就绪", en: "Setup is ready"' in installer_handoff,
    )
    record(
        checks,
        "installer_handoff_fragment_scrubbed",
        'params.get("agentops-owner-setup")' in auth_gate and "window.history.replaceState" in auth_gate,
    )
    record(
        checks,
        "setup_code_remains_required_authority",
        "setup_code: string;" in live_api,
    )
    record(
        checks,
        "password_recovery_uses_dedicated_api_contract",
        "startHumanPasswordRecovery" in auth_gate
        and "startHumanPasswordRecovery(setupCode)" in auth_gate
        and "completeHumanPasswordRecovery" in auth_gate
        and '"/human-auth/password-recovery/start"' in live_api
        and "JSON.stringify({ setup_code: setupCode })" in live_api
        and '"/human-auth/password-recovery/complete"' in live_api
        and "recovery_authority: string;" in live_api,
    )
    record(
        checks,
        "non_human_401_does_not_expire_browser_session",
        'const HUMAN_SESSION_UNAUTHORIZED_ERRORS = new Set([' in live_api
        and '"human_auth_required"' in live_api
        and '"human_session_invalid"' in live_api
        and '"human_session_expired"' in live_api
        and "await isHumanSessionUnauthorized(response)" in live_api
        and 'response.status === 401 && !path.startsWith("/human-auth/")' not in live_api,
    )
    record(
        checks,
        "recovery_authority_stays_in_component_memory",
        'const [recoveryAuthority, setRecoveryAuthority] = useState("");' in auth_gate
        and "setRecoveryAuthority(recovery.recovery_authority);" in auth_gate
        and "sessionStorage.setItem" not in auth_gate
        and "localStorage.setItem" not in auth_gate,
    )

    record(checks, "app_shell_accepts_locked", "locked?: boolean;" in app_shell)
    record(checks, "app_shell_locks_sidebar", "<Sidebar locked={locked} />" in app_shell)
    record(
        checks,
        "app_shell_locks_topbar",
        "<Topbar locked={locked} lockLabel={lockLabel} />" in app_shell,
    )

    sidebar_locked_render = slice_between(sidebar, "{locked ? (\n                          <div", ") : (\n                          <NavLink")
    sidebar_unlocked_render = slice_between(sidebar, ") : (\n                          <NavLink", "</NavLink>")
    record(
        checks,
        "locked_sidebar_preserves_workspace_information_architecture",
        "{navGroups.map((group) =>" in sidebar
        and 'aria-current={isActive ? "page" : undefined}' in sidebar_locked_render
        and 'aria-disabled="true"' in sidebar_locked_render
        and 'item.path === "/workspace/account"' in sidebar,
    )
    record(checks, "locked_sidebar_item_is_not_link", "NavLink" not in sidebar_locked_render)
    record(
        checks,
        "locked_sidebar_has_no_setup_only_alternate_navigation",
        "copy.setupGroup" not in sidebar
        and "copy.setupHint" not in sidebar
        and 'aria-current="page"' not in sidebar,
    )
    record(checks, "unlocked_sidebar_keeps_navigation", "<NavLink" in sidebar_unlocked_render)

    topbar_persistent_controls = slice_between(topbar, "{/* Right */}", "{/* Notifications */}")
    record(
        checks,
        "locked_topbar_keeps_theme_control",
        "onClick={cycleTheme}" in topbar_persistent_controls
        and "title={copy.switchTheme}" in topbar_persistent_controls,
    )
    record(
        checks,
        "locked_topbar_keeps_language_control",
        'onClick={() => setLocale(locale === "en" ? "zh" : "en")}' in topbar_persistent_controls
        and "title={copy.switchLanguage}" in topbar_persistent_controls,
    )
    logout_control = slice_between(topbar, "{humanAuthRequired && !locked && (", "\n        )}")
    record(checks, "locked_topbar_hides_logout", "logout()" in logout_control and "<LogOut" in logout_control)
    record(
        checks,
        "locked_topbar_hides_placeholder_avatar",
        ") : !locked ? (" in topbar
        and "title={displayName}" in topbar,
    )
    record(
        checks,
        "locked_topbar_uses_private_host_workspace",
        "const workspaceName = locked ? copy.privateHost" in topbar
        and "{workspaceName}" in topbar,
    )
    record(
        checks,
        "locked_topbar_omits_fake_search",
        "{locked ? (\n        <div className=\"hidden flex-1 md:block\" />" in topbar
        and "searchLocked" not in topbar,
    )

    failures.extend(name for name, passed in checks.items() if not passed)
    output = {
        "operation": "private_host_auth_workspace_ui_smoke",
        "ok": not failures,
        "files": [str(path.relative_to(ROOT)) for path in FILES],
        "checks": checks,
        "failure_count": len(failures),
        "failures": failures,
        "safety": {
            "static_only": True,
            "read_only": True,
            "secret_data_inspected": False,
            "secret_data_output": False,
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
