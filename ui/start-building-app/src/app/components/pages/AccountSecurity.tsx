import { useCallback, useEffect, useMemo, useState } from "react";
import { Ban, CheckCircle2, Copy, KeyRound, LoaderCircle, LogOut, Monitor, Power, RefreshCw, ShieldCheck, Smartphone, UserPlus, X } from "lucide-react";
import {
  confirmHostRelayTransition,
  createHumanPairingInvitation,
  loadHostRelay,
  loadHumanBrowserSessions,
  loadHumanPairedDevices,
  loadHumanPairingInvitations,
  prepareHostRelayTransition,
  revokeHumanBrowserSession,
  revokeHumanPairedDevice,
  revokeHumanPairingInvitation,
  type HostRelayAction,
  type HostRelayDisplayState,
  type HostRelayStatusPayload,
  type HumanBrowserSession,
  type HumanBrowserSessionsPayload,
  type HumanPairedDevicesPayload,
  type HumanPairingInvitationCreated,
  type HumanPairingInvitationsPayload,
  type HumanPairingRole,
} from "../../data/liveApi";
import { useHumanAuth } from "../../context/HumanAuthContext";
import { pick, usePreferences } from "../../context/PreferencesContext";
import { WorkspaceSettingsPage, WorkspaceSettingsSection } from "../shared/WorkspaceSettings";

function formatDate(value: string | null | undefined, locale: "zh" | "en") {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(locale === "zh" ? "zh-CN" : "en-US");
}

function statusColor(status: HumanBrowserSession["status"]) {
  if (status === "active") return "var(--mis-success)";
  if (status === "expired") return "var(--mis-warning)";
  return "var(--mis-muted)";
}

function boundedRef(value: string) {
  return value.length <= 72 ? value : `${value.slice(0, 48)}...${value.slice(-12)}`;
}

function pairingErrorMessage(locale: "zh" | "en") {
  return pick(locale, {
    zh: "配对请求未完成。请刷新后重试。",
    en: "The pairing request did not complete. Refresh and try again.",
  });
}

function relayErrorMessage(locale: "zh" | "en") {
  return pick(locale, {
    zh: "远程操控台请求未完成。请刷新状态后重试。",
    en: "The Remote Console request did not complete. Refresh the status and try again.",
  });
}

function relayStatusColor(state: HostRelayDisplayState) {
  if (state === "enabled") return "var(--mis-success)";
  if (state === "prepared" || state === "rolled_back") return "var(--mis-primary)";
  if (state === "rollback_failed") return "#F87171";
  if (state === "pending" || state === "restart_scheduled" || state === "manual_restart_required" || state === "restart_required") return "var(--mis-warning)";
  return "var(--mis-muted)";
}

export function AccountSecurity() {
  const { locale } = usePreferences();
  const { required, user, logout } = useHumanAuth();
  const [payload, setPayload] = useState<HumanBrowserSessionsPayload | null>(null);
  const [invitations, setInvitations] = useState<HumanPairingInvitationsPayload | null>(null);
  const [devices, setDevices] = useState<HumanPairedDevicesPayload | null>(null);
  const [createdInvitation, setCreatedInvitation] = useState<HumanPairingInvitationCreated | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [pairingError, setPairingError] = useState("");
  const [pairingRole, setPairingRole] = useState<HumanPairingRole>("operator");
  const [pairingExpiry, setPairingExpiry] = useState(3600);
  const [pairingLabel, setPairingLabel] = useState("");
  const [copied, setCopied] = useState(false);
  const [relayStatus, setRelayStatus] = useState<HostRelayStatusPayload | null>(null);
  const [relayTransition, setRelayTransition] = useState<{ action: HostRelayAction; ref: string } | null>(null);
  const [relayLoading, setRelayLoading] = useState(true);
  const [relayBusy, setRelayBusy] = useState(false);
  const [relayError, setRelayError] = useState("");

  const copy = pick(locale, {
    zh: {
      title: "账户与访问",
      subtitle: "管理这台主机上的账户和浏览器会话",
      account: "当前账户",
      accountHint: "人类浏览器账户与 Agent 机器凭据相互独立。",
      name: "显示名称",
      username: "用户名",
      role: "角色",
      workspace: "工作区",
      sessions: "浏览器会话",
      sessionsHint: "查看并撤销其他已登录浏览器。系统不采集 IP 或 User-Agent。",
      pairing: "成员与设备配对",
      pairingHint: "创建一次性邀请，为非 Owner 成员设置账户并绑定当前设备。",
      createInvitation: "创建邀请",
      roleOperator: "操作员",
      roleApprover: "审批员",
      roleViewer: "只读成员",
      invitationLabel: "邀请备注",
      invitationLabelPlaceholder: "例如：小王的 MacBook",
      validFor: "有效期",
      tenMinutes: "10 分钟",
      oneHour: "1 小时",
      thirtyMinutes: "30 分钟",
      oneTimeLink: "一次性配对链接",
      oneTimeLinkHint: "密钥仅在本次创建响应中出现，并只保留在当前页面内存中。",
      copyLink: "复制配对链接",
      copied: "已复制",
      dismiss: "清除",
      invitations: "配对邀请",
      devices: "已配对设备",
      noInvitations: "暂无配对邀请。",
      noDevices: "暂无已配对设备。",
      invitation: "邀请",
      device: "设备",
      label: "备注",
      redeemed: "已使用",
      locked: "已锁定",
      confirmInvitation: "确定撤销这个配对邀请吗？",
      confirmDevice: "确定撤销这个设备吗？该设备需要重新配对或登录。",
      pairingBoundary: "页面仅显示有界引用。配对密钥不会进入浏览器存储、日志或错误信息。",
      active: "有效",
      total: "总计",
      revoked: "已撤销",
      expired: "已过期",
      current: "当前会话",
      otherSession: "其他浏览器会话",
      created: "创建时间",
      lastSeen: "最近活动",
      expires: "到期时间",
      revoke: "撤销",
      revokeOther: "撤销其他会话",
      refresh: "刷新",
      signOut: "退出当前会话",
      confirmOne: "确定撤销这个浏览器会话吗？该浏览器需要重新登录。",
      confirmOther: "确定撤销除当前浏览器之外的所有会话吗？",
      ownerOnly: "只有 Owner 可以查看和撤销浏览器会话。",
      authDisabled: "当前主机未启用人类身份认证。",
      empty: "没有可显示的浏览器会话。",
      safeRef: "安全引用",
      boundary: "Cookie、原始 Session ID、Session Hash 和 Token 不会显示在此页面或写入审计元数据。",
      relayTitle: "远程操控台",
      relayHint: "由 Owner 分两步准备并确认 Relay 配置变更。当前界面不代表 Relay 已部署或远程地址已可用。",
      relayStatus: "状态",
      relayDisabled: "已关闭",
      relayEnabled: "已启用",
      relayPrepared: "等待明确确认",
      relayPending: "正在处理",
      relayRestartScheduled: "正在安全重启主机服务",
      relayManualRestartRequired: "需要手动重启主机服务",
      relayRestartRequired: "需要重启主机服务",
      relayRolledBack: "新配置启动失败，已恢复原配置",
      relayRollbackFailed: "自动恢复失败，需要本机处理",
      relayUnavailable: "暂不可用",
      relayEnable: "准备启用",
      relayDisable: "准备停用",
      relayConfirmEnable: "确认启用",
      relayConfirmDisable: "确认停用",
      relayPreparedHint: "准备步骤已完成。请再次明确确认本次变更。",
      relayOwnerOnly: "只有 Owner 可以更改远程操控台状态。",
      relayBoundary: "页面不会显示或保存连接配置、加密材料或机器凭据。确认引用仅保存在当前页面内存中。",
    },
    en: {
      title: "Account and access",
      subtitle: "Manage the account and browser sessions on this host",
      account: "Current account",
      accountHint: "Human browser accounts remain separate from Agent machine credentials.",
      name: "Display name",
      username: "Username",
      role: "Role",
      workspace: "Workspace",
      sessions: "Browser sessions",
      sessionsHint: "Review and revoke other signed-in browsers. IP and User-Agent data are not collected.",
      pairing: "Members and paired devices",
      pairingHint: "Create a one-time invitation that provisions a non-Owner account and binds the current device.",
      createInvitation: "Create invitation",
      roleOperator: "Operator",
      roleApprover: "Approver",
      roleViewer: "Viewer",
      invitationLabel: "Invitation label",
      invitationLabelPlaceholder: "For example: Casey's MacBook",
      validFor: "Valid for",
      tenMinutes: "10 minutes",
      oneHour: "1 hour",
      thirtyMinutes: "30 minutes",
      oneTimeLink: "One-time pairing link",
      oneTimeLinkHint: "The secret appears only in this create response and remains only in this page's memory.",
      copyLink: "Copy pairing link",
      copied: "Copied",
      dismiss: "Clear",
      invitations: "Pairing invitations",
      devices: "Paired devices",
      noInvitations: "No pairing invitations.",
      noDevices: "No paired devices.",
      invitation: "Invitation",
      device: "Device",
      label: "Label",
      redeemed: "Used",
      locked: "Locked",
      confirmInvitation: "Revoke this pairing invitation?",
      confirmDevice: "Revoke this device? It will need to pair or sign in again.",
      pairingBoundary: "Only bounded references are shown. Pairing secrets never enter browser storage, logs, or error messages.",
      active: "Active",
      total: "total",
      revoked: "Revoked",
      expired: "Expired",
      current: "Current session",
      otherSession: "Other browser session",
      created: "Created",
      lastSeen: "Last active",
      expires: "Expires",
      revoke: "Revoke",
      revokeOther: "Revoke other sessions",
      refresh: "Refresh",
      signOut: "Sign out current session",
      confirmOne: "Revoke this browser session? That browser will need to sign in again.",
      confirmOther: "Revoke every browser session except the current one?",
      ownerOnly: "Only an Owner can view and revoke browser sessions.",
      authDisabled: "Human authentication is not enabled on this host.",
      empty: "No browser sessions to display.",
      safeRef: "Safe reference",
      boundary: "Cookies, raw Session IDs, Session hashes, and tokens are never displayed here or written to audit metadata.",
      relayTitle: "Remote Console",
      relayHint: "An Owner prepares and explicitly confirms Relay configuration changes in two steps. This screen does not mean a Relay is deployed or a remote address is available.",
      relayStatus: "Status",
      relayDisabled: "Disabled",
      relayEnabled: "Enabled",
      relayPrepared: "Awaiting explicit confirmation",
      relayPending: "Pending",
      relayRestartScheduled: "Safely restarting the Host service",
      relayManualRestartRequired: "Manual Host service restart required",
      relayRestartRequired: "Host service restart required",
      relayRolledBack: "New configuration failed; original configuration restored",
      relayRollbackFailed: "Automatic recovery failed; local action required",
      relayUnavailable: "Unavailable",
      relayEnable: "Prepare enable",
      relayDisable: "Prepare disable",
      relayConfirmEnable: "Confirm enable",
      relayConfirmDisable: "Confirm disable",
      relayPreparedHint: "The preparation step is complete. Explicitly confirm this change once more.",
      relayOwnerOnly: "Only an Owner can change Remote Console state.",
      relayBoundary: "Connection configuration, cryptographic material, and machine credentials are never displayed or stored here. The confirmation reference stays only in this page's memory.",
    },
  });

  const canManageSessions = required && user?.role === "owner";
  const canManageRelay = required && user?.role === "owner";

  const refreshRelay = useCallback(async () => {
    if (!canManageRelay) {
      setRelayStatus(null);
      setRelayTransition(null);
      setRelayLoading(false);
      return;
    }
    setRelayLoading(true);
    setRelayError("");
    try {
      setRelayStatus(await loadHostRelay());
    } catch {
      setRelayStatus({ state: "unavailable", active_enabled: false, control_available: false, transition_pending: false, restart_required: false });
      setRelayError(relayErrorMessage(locale));
    } finally {
      setRelayLoading(false);
    }
  }, [canManageRelay, locale]);

  const refresh = useCallback(async () => {
    if (!canManageSessions) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [nextSessions, nextInvitations, nextDevices] = await Promise.all([
        loadHumanBrowserSessions(),
        loadHumanPairingInvitations(),
        loadHumanPairedDevices(),
      ]);
      setPayload(nextSessions);
      setInvitations(nextInvitations);
      setDevices(nextDevices);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [canManageSessions]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { void refreshRelay(); }, [refreshRelay]);

  const activeOtherCount = useMemo(
    () => payload?.sessions.filter((session) => session.status === "active" && !session.current).length || 0,
    [payload],
  );

  const revoke = async (sessionRef?: string) => {
    const allOther = !sessionRef;
    if (!window.confirm(allOther ? copy.confirmOther : copy.confirmOne)) return;
    setBusy(sessionRef || "all_other");
    setError("");
    try {
      await revokeHumanBrowserSession(allOther ? { all_other: true } : { session_ref: sessionRef });
      await refresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setBusy("");
    }
  };

  const createInvitation = async () => {
    setBusy("create_invitation");
    setPairingError("");
    setCopied(false);
    try {
      const created = await createHumanPairingInvitation({
        role: pairingRole,
        expires_in_seconds: pairingExpiry,
        label: pairingLabel.trim() || undefined,
      });
      if (!created.pairing_secret || (created.secret_omitted !== false && created.pairing_secret_omitted !== false)) {
        throw new Error("pairing_secret_missing");
      }
      setCreatedInvitation(created);
      setPairingLabel("");
      await refresh();
    } catch {
      setPairingError(pairingErrorMessage(locale));
    } finally {
      setBusy("");
    }
  };

  const copyPairingLink = async () => {
    if (!createdInvitation?.pairing_secret) return;
    setPairingError("");
    try {
      const target = new URL(window.location.href);
      target.hash = "";
      await navigator.clipboard.writeText(`${target.toString()}#pair=${encodeURIComponent(createdInvitation.pairing_secret)}`);
      setCopied(true);
    } catch {
      setPairingError(pairingErrorMessage(locale));
    }
  };

  const revokeInvitation = async (invitationRef: string) => {
    if (!window.confirm(copy.confirmInvitation)) return;
    setBusy(`invitation:${invitationRef}`);
    setPairingError("");
    try {
      await revokeHumanPairingInvitation(invitationRef);
      if (createdInvitation?.invitation_ref === invitationRef) setCreatedInvitation(null);
      await refresh();
    } catch {
      setPairingError(pairingErrorMessage(locale));
    } finally {
      setBusy("");
    }
  };

  const revokeDevice = async (deviceRef: string) => {
    if (!window.confirm(copy.confirmDevice)) return;
    setBusy(`device:${deviceRef}`);
    setPairingError("");
    try {
      await revokeHumanPairedDevice(deviceRef);
      await refresh();
    } catch {
      setPairingError(pairingErrorMessage(locale));
    } finally {
      setBusy("");
    }
  };

  const roleLabel = (role: HumanPairingRole) => ({
    operator: copy.roleOperator,
    approver: copy.roleApprover,
    viewer: copy.roleViewer,
  })[role];

  const relayState = relayBusy
    ? "pending"
    : relayTransition
      ? "prepared"
      : relayStatus?.state || "unavailable";
  const relayAction: HostRelayAction = relayStatus?.active_enabled ? "disable" : "enable";
  const relayStatusLabel = ({
    disabled: copy.relayDisabled,
    enabled: copy.relayEnabled,
    prepared: copy.relayPrepared,
    pending: copy.relayPending,
    restart_scheduled: copy.relayRestartScheduled,
    manual_restart_required: copy.relayManualRestartRequired,
    restart_required: copy.relayRestartRequired,
    rolled_back: copy.relayRolledBack,
    rollback_failed: copy.relayRollbackFailed,
    unavailable: copy.relayUnavailable,
  } as const)[relayState];
  const relayControlsBlocked = !canManageRelay
    || relayLoading
    || relayBusy
    || relayStatus?.control_available === false
    || relayStatus?.transition_pending === true
    || relayStatus?.restart_required === true
    || relayStatus?.restart_pending === true
    || relayStatus?.state === "rollback_failed"
    || relayStatus?.state === "unavailable";

  const prepareRelay = async () => {
    if (relayControlsBlocked || relayTransition) return;
    setRelayBusy(true);
    setRelayError("");
    try {
      const transition = await prepareHostRelayTransition(relayAction);
      setRelayTransition({ action: relayAction, ref: transition.transition_ref });
      setRelayStatus(transition);
    } catch {
      await refreshRelay();
      setRelayError(relayErrorMessage(locale));
    } finally {
      setRelayBusy(false);
    }
  };

  const confirmRelay = async () => {
    if (!canManageRelay || relayBusy || !relayTransition) return;
    setRelayBusy(true);
    setRelayError("");
    try {
      const nextStatus = await confirmHostRelayTransition(relayTransition.ref, relayTransition.action);
      setRelayTransition(null);
      setRelayStatus(nextStatus);
      if (!nextStatus.restart_required) await refreshRelay();
    } catch {
      setRelayError(relayErrorMessage(locale));
    } finally {
      setRelayBusy(false);
    }
  };

  return (
    <WorkspaceSettingsPage title={copy.title} subtitle={copy.subtitle} testId="account-security-page">
      <WorkspaceSettingsSection title={copy.account} description={copy.accountHint} testId="account-profile-section">
        <dl className="border-t text-xs" style={{ borderColor: "var(--mis-border)" }}>
          {[
            [copy.name, user?.display_name || "-"],
            [copy.username, user?.username || "-"],
            [copy.role, user?.role || "-"],
            [copy.workspace, user?.workspace_id || "-"],
          ].map(([label, value]) => (
            <div key={label} className="grid gap-1 border-b py-3 md:grid-cols-[180px_minmax(0,1fr)]" style={{ borderColor: "var(--mis-border)" }}>
              <dt style={{ color: "var(--mis-muted)" }}>{label}</dt>
              <dd className="font-medium" style={{ color: "var(--mis-text)" }}>{value}</dd>
            </div>
          ))}
        </dl>
      </WorkspaceSettingsSection>

      <WorkspaceSettingsSection
        title={copy.relayTitle}
        description={copy.relayHint}
        testId="host-relay-section"
        meta={(
          <p className="mt-2 text-[11px] font-medium" style={{ color: relayStatusColor(relayState) }} data-testid="host-relay-status">
            {copy.relayStatus}: {relayStatusLabel}
          </p>
        )}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4" style={{ borderColor: "var(--mis-border)" }}>
            <div className="flex min-w-0 items-start gap-2 text-xs" style={{ color: "var(--mis-dim)" }}>
              <Monitor size={15} className="mt-0.5 shrink-0" style={{ color: relayStatusColor(relayState) }} />
              <div>
                <p className="font-semibold" style={{ color: "var(--mis-text)" }}>{relayStatusLabel}</p>
                <p className="mt-1 text-[11px] leading-5" style={{ color: "var(--mis-muted)" }}>
                  {canManageRelay ? (relayTransition ? copy.relayPreparedHint : copy.relayHint) : copy.relayOwnerOnly}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => void refreshRelay()}
                disabled={!canManageRelay || relayLoading || relayBusy}
                className="inline-flex h-8 w-8 items-center justify-center rounded border disabled:opacity-50"
                style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)", background: "var(--mis-surface)" }}
                title={copy.refresh}
                aria-label={copy.refresh}
              >
                <RefreshCw size={14} className={relayLoading ? "animate-spin" : ""} />
              </button>
              {relayTransition ? (
                <button
                  type="button"
                  onClick={() => void confirmRelay()}
                  disabled={!canManageRelay || relayBusy}
                  className="inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-xs font-semibold disabled:opacity-50"
                  style={{ background: "var(--mis-primary)", color: "#fff" }}
                  data-testid="host-relay-confirm"
                >
                  {relayBusy ? <LoaderCircle size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
                  {relayTransition.action === "enable" ? copy.relayConfirmEnable : copy.relayConfirmDisable}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void prepareRelay()}
                  disabled={relayControlsBlocked}
                  className="inline-flex h-8 items-center gap-1.5 rounded border px-2.5 text-xs font-semibold disabled:opacity-50"
                  style={{ borderColor: "var(--mis-border)", color: "var(--mis-primary)", background: "var(--mis-surface)" }}
                  data-testid="host-relay-prepare"
                >
                  {relayBusy ? <LoaderCircle size={14} className="animate-spin" /> : <Power size={14} />}
                  {relayAction === "enable" ? copy.relayEnable : copy.relayDisable}
                </button>
              )}
            </div>
          </div>
          {relayError && <div role="alert" className="border-b py-3 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)" }}>{relayError}</div>}
          <p className="pt-4 text-[11px] leading-5" style={{ color: "var(--mis-muted)" }}>{copy.relayBoundary}</p>
        </div>
      </WorkspaceSettingsSection>

      <WorkspaceSettingsSection
        title={copy.pairing}
        description={copy.pairingHint}
        testId="human-pairing-section"
        meta={canManageSessions && (
          <p className="mt-2 text-[11px]" style={{ color: "var(--mis-muted)" }}>
            {invitations?.active_count ?? invitations?.invitations.filter((item) => item.status === "active").length ?? 0} {copy.active} · {devices?.active_count ?? devices?.devices.filter((item) => item.status === "active").length ?? 0} {copy.devices}
          </p>
        )}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 border-b pb-3 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>
            <KeyRound size={15} style={{ color: canManageSessions ? "var(--mis-success)" : "var(--mis-muted)" }} />
            {canManageSessions ? copy.pairingHint : required ? copy.ownerOnly : copy.authDisabled}
          </div>

          {canManageSessions && (
            <form
              onSubmit={(event) => { event.preventDefault(); void createInvitation(); }}
              className="grid gap-3 border-b py-4 sm:grid-cols-2 lg:grid-cols-[minmax(120px,0.8fr)_minmax(140px,0.8fr)_minmax(180px,1.5fr)_auto] lg:items-end"
              style={{ borderColor: "var(--mis-border)" }}
              data-testid="pairing-invitation-form"
            >
              <label className="min-w-0 text-[11px]" style={{ color: "var(--mis-muted)" }}>
                {copy.role}
                <select
                  value={pairingRole}
                  onChange={(event) => setPairingRole(event.target.value as HumanPairingRole)}
                  className="mt-1 h-9 w-full rounded border px-2.5 text-xs outline-none"
                  style={{ borderColor: "var(--mis-border)", background: "var(--mis-surface)", color: "var(--mis-text)" }}
                >
                  <option value="operator">{copy.roleOperator}</option>
                  <option value="approver">{copy.roleApprover}</option>
                  <option value="viewer">{copy.roleViewer}</option>
                </select>
              </label>
              <label className="min-w-0 text-[11px]" style={{ color: "var(--mis-muted)" }}>
                {copy.validFor}
                <select
                  value={pairingExpiry}
                  onChange={(event) => setPairingExpiry(Number(event.target.value))}
                  className="mt-1 h-9 w-full rounded border px-2.5 text-xs outline-none"
                  style={{ borderColor: "var(--mis-border)", background: "var(--mis-surface)", color: "var(--mis-text)" }}
                >
                  <option value={600}>{copy.tenMinutes}</option>
                  <option value={1800}>{copy.thirtyMinutes}</option>
                  <option value={3600}>{copy.oneHour}</option>
                </select>
              </label>
              <label className="min-w-0 text-[11px]" style={{ color: "var(--mis-muted)" }}>
                {copy.invitationLabel}
                <input
                  value={pairingLabel}
                  onChange={(event) => setPairingLabel(event.target.value)}
                  maxLength={80}
                  placeholder={copy.invitationLabelPlaceholder}
                  className="mt-1 h-9 w-full rounded border px-3 text-xs outline-none"
                  style={{ borderColor: "var(--mis-border)", background: "var(--mis-surface)", color: "var(--mis-text)" }}
                />
              </label>
              <button
                type="submit"
                disabled={Boolean(busy) || loading}
                className="inline-flex h-9 items-center justify-center gap-2 rounded px-3 text-xs font-semibold disabled:opacity-50"
                style={{ background: "var(--mis-primary)", color: "#fff" }}
              >
                {busy === "create_invitation" ? <LoaderCircle size={15} className="animate-spin" /> : <UserPlus size={15} />}
                {copy.createInvitation}
              </button>
            </form>
          )}

          {createdInvitation && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-b py-4" style={{ borderColor: "var(--mis-border)" }} data-testid="pairing-secret-ready">
              <div className="min-w-0">
                <p className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>{copy.oneTimeLink}</p>
                <p className="mt-1 text-[11px] leading-5" style={{ color: "var(--mis-muted)" }}>{copy.oneTimeLinkHint}</p>
                <p className="mt-1 break-all text-[11px]" style={{ color: "var(--mis-dim)" }}>{copy.safeRef}: <code>{boundedRef(createdInvitation.invitation_ref)}</code></p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void copyPairingLink()}
                  className="inline-flex h-8 items-center gap-1.5 rounded border px-2.5 text-xs"
                  style={{ borderColor: "var(--mis-border)", color: "var(--mis-primary)", background: "var(--mis-surface)" }}
                >
                  <Copy size={14} /> {copied ? copy.copied : copy.copyLink}
                </button>
                <button
                  type="button"
                  onClick={() => { setCreatedInvitation(null); setCopied(false); }}
                  className="inline-flex h-8 w-8 items-center justify-center rounded border"
                  style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)", background: "var(--mis-surface)" }}
                  title={copy.dismiss}
                  aria-label={copy.dismiss}
                >
                  <X size={14} />
                </button>
              </div>
            </div>
          )}

          {pairingError && <div role="alert" className="border-b py-3 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)" }}>{pairingError}</div>}

          {canManageSessions && (
            <div className="grid gap-x-6 lg:grid-cols-2">
              <div className="min-w-0">
                <h3 className="flex items-center gap-2 border-b py-3 text-xs font-semibold" style={{ borderColor: "var(--mis-border)", color: "var(--mis-text)" }}>
                  <KeyRound size={14} /> {copy.invitations}
                </h3>
                {invitations?.invitations.length ? invitations.invitations.map((invitation) => (
                  <article key={invitation.invitation_ref} className="border-b py-3" style={{ borderColor: "var(--mis-border)" }} data-testid="pairing-invitation-row">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 text-[11px]">
                        <p className="font-semibold" style={{ color: "var(--mis-text)" }}>{invitation.label || copy.invitation}</p>
                        <p className="mt-1 break-all" style={{ color: "var(--mis-muted)" }}><code>{boundedRef(invitation.invitation_ref)}</code></p>
                        <p className="mt-1" style={{ color: "var(--mis-dim)" }}>{roleLabel(invitation.role)} · {copy[invitation.status]} · {copy.expires} {formatDate(invitation.expires_at, locale)}</p>
                      </div>
                      {invitation.status === "active" && (
                        <button
                          type="button"
                          onClick={() => void revokeInvitation(invitation.invitation_ref)}
                          disabled={Boolean(busy)}
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border disabled:opacity-50"
                          style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)", background: "var(--mis-surface)" }}
                          title={copy.revoke}
                          aria-label={copy.revoke}
                        >
                          {busy === `invitation:${invitation.invitation_ref}` ? <LoaderCircle size={14} className="animate-spin" /> : <Ban size={14} />}
                        </button>
                      )}
                    </div>
                  </article>
                )) : <p className="border-b py-4 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>{copy.noInvitations}</p>}
              </div>

              <div className="min-w-0">
                <h3 className="flex items-center gap-2 border-b py-3 text-xs font-semibold" style={{ borderColor: "var(--mis-border)", color: "var(--mis-text)" }}>
                  <Smartphone size={14} /> {copy.devices}
                </h3>
                {devices?.devices.length ? devices.devices.map((device) => (
                  <article key={device.device_ref} className="border-b py-3" style={{ borderColor: "var(--mis-border)" }} data-testid="paired-device-row">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 text-[11px]">
                        <p className="font-semibold" style={{ color: "var(--mis-text)" }}>{device.label || device.display_name || device.username || copy.device}</p>
                        <p className="mt-1 break-all" style={{ color: "var(--mis-muted)" }}><code>{boundedRef(device.device_ref)}</code></p>
                        <p className="mt-1" style={{ color: "var(--mis-dim)" }}>{roleLabel(device.role)} · {copy[device.status]} · {copy.lastSeen} {formatDate(device.last_seen_at, locale)}</p>
                      </div>
                      {device.status === "active" && (
                        <button
                          type="button"
                          onClick={() => void revokeDevice(device.device_ref)}
                          disabled={Boolean(busy)}
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border disabled:opacity-50"
                          style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)", background: "var(--mis-surface)" }}
                          title={copy.revoke}
                          aria-label={copy.revoke}
                        >
                          {busy === `device:${device.device_ref}` ? <LoaderCircle size={14} className="animate-spin" /> : <Ban size={14} />}
                        </button>
                      )}
                    </div>
                  </article>
                )) : <p className="border-b py-4 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>{copy.noDevices}</p>}
              </div>
            </div>
          )}

          <p className="pt-4 text-[11px] leading-5" style={{ color: "var(--mis-muted)" }}>{copy.pairingBoundary}</p>
        </div>
      </WorkspaceSettingsSection>

      <WorkspaceSettingsSection
        title={copy.sessions}
        description={copy.sessionsHint}
        testId="browser-session-section"
        meta={payload && (
            <p className="mt-2 text-[11px]" style={{ color: "var(--mis-muted)" }}>
              {payload.active_count} {copy.active} · {payload.session_count} {copy.total}
            </p>
        )}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-3" style={{ borderColor: "var(--mis-border)" }}>
            <div className="flex items-center gap-2 text-xs" style={{ color: "var(--mis-dim)" }}>
              <ShieldCheck size={15} style={{ color: "var(--mis-success)" }} />
              {canManageSessions ? copy.sessions : required ? copy.ownerOnly : copy.authDisabled}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={!canManageSessions || loading}
                className="inline-flex h-8 w-8 items-center justify-center rounded border disabled:opacity-50"
                style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)", background: "var(--mis-surface)" }}
                title={copy.refresh}
                aria-label={copy.refresh}
              >
                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              </button>
              <button
                type="button"
                onClick={() => void revoke()}
                disabled={!canManageSessions || activeOtherCount === 0 || Boolean(busy)}
                className="inline-flex h-8 items-center gap-1.5 rounded border px-2.5 text-xs disabled:opacity-50"
                style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)", background: "var(--mis-surface)" }}
              >
                {busy === "all_other" ? <LoaderCircle size={14} className="animate-spin" /> : <Ban size={14} />}
                {copy.revokeOther}
              </button>
            </div>
          </div>

          {error && <div role="alert" className="border-b py-3 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)" }}>{error}</div>}
          {loading ? (
            <div className="flex items-center gap-2 border-b py-5 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>
              <LoaderCircle size={15} className="animate-spin" /> {copy.refresh}
            </div>
          ) : canManageSessions && payload?.sessions.length ? (
            <div>
              {payload.sessions.map((session) => (
                <article key={session.session_ref} className="border-b py-4" style={{ borderColor: "var(--mis-border)" }} data-testid="browser-session-row">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Monitor size={15} style={{ color: statusColor(session.status) }} />
                        <span className="text-xs font-semibold" style={{ color: "var(--mis-text)" }}>
                          {session.current ? copy.current : copy.otherSession}
                        </span>
                        <span className="text-[10px] font-medium uppercase" style={{ color: statusColor(session.status) }}>
                          {copy[session.status]}
                        </span>
                      </div>
                      <div className="mt-2 text-[11px]" style={{ color: "var(--mis-muted)" }}>
                        {copy.safeRef}: <code>{session.session_ref}</code>
                      </div>
                    </div>
                    {session.current ? (
                      <span className="inline-flex items-center gap-1.5 text-[11px]" style={{ color: "var(--mis-success)" }}>
                        <CheckCircle2 size={13} /> {copy.current}
                      </span>
                    ) : session.status === "active" ? (
                      <button
                        type="button"
                        onClick={() => void revoke(session.session_ref)}
                        disabled={Boolean(busy)}
                        className="inline-flex h-8 items-center gap-1.5 rounded border px-2.5 text-xs disabled:opacity-50"
                        style={{ borderColor: "var(--mis-border)", color: "var(--mis-warning)", background: "var(--mis-surface)" }}
                      >
                        {busy === session.session_ref ? <LoaderCircle size={14} className="animate-spin" /> : <Ban size={14} />}
                        {copy.revoke}
                      </button>
                    ) : null}
                  </div>
                  <dl className="mt-3 grid gap-2 text-[11px] sm:grid-cols-3">
                    <div><dt style={{ color: "var(--mis-muted)" }}>{copy.created}</dt><dd className="mt-0.5" style={{ color: "var(--mis-dim)" }}>{formatDate(session.created_at, locale)}</dd></div>
                    <div><dt style={{ color: "var(--mis-muted)" }}>{copy.lastSeen}</dt><dd className="mt-0.5" style={{ color: "var(--mis-dim)" }}>{formatDate(session.last_seen_at, locale)}</dd></div>
                    <div><dt style={{ color: "var(--mis-muted)" }}>{copy.expires}</dt><dd className="mt-0.5" style={{ color: "var(--mis-dim)" }}>{formatDate(session.expires_at, locale)}</dd></div>
                  </dl>
                </article>
              ))}
            </div>
          ) : canManageSessions ? (
            <div className="border-b py-5 text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>{copy.empty}</div>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-3 pt-4">
            <p className="max-w-xl text-[11px] leading-5" style={{ color: "var(--mis-muted)" }}>{copy.boundary}</p>
            {required && (
              <button
                type="button"
                onClick={() => void logout()}
                className="inline-flex h-8 items-center gap-1.5 rounded border px-2.5 text-xs"
                style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)", background: "var(--mis-surface)" }}
              >
                <LogOut size={14} /> {copy.signOut}
              </button>
            )}
          </div>
        </div>
      </WorkspaceSettingsSection>
    </WorkspaceSettingsPage>
  );
}
