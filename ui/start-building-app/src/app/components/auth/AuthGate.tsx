import { FormEvent, ReactNode, useCallback, useEffect, useId, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
} from "lucide-react";
import {
  bootstrapHuman,
  completeHumanPasswordRecovery,
  getHumanAuthStatus,
  HUMAN_AUTH_UNAUTHORIZED_EVENT,
  loginHuman,
  logoutHuman,
  pairHuman,
  setHumanAuthCsrf,
  startHumanPasswordRecovery,
  type HumanAuthStatus,
} from "../../data/liveApi";
import { HumanAuthContext } from "../../context/HumanAuthContext";
import { pick, usePreferences } from "../../context/PreferencesContext";
import { AppShell } from "../layout/AppShell";
import { WorkspaceSettingsPage, WorkspaceSettingsSection } from "../shared/WorkspaceSettings";

type GateState = "checking" | "login" | "bootstrap" | "recovery" | "pairing" | "ready" | "unavailable";
type OwnerSetupHandoff = { seen: boolean; value: string };
type PairingHandoff = { seen: boolean; value: string };

function errorCode(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/^[a-z0-9_]{1,64}$/.test(message)) return message;
  const match = message.match(/"error"\s*:\s*"([^"]+)"/);
  return match?.[1] || "unknown";
}

function consumeOwnerSetupHandoff(): OwnerSetupHandoff {
  if (typeof window === "undefined" || !window.location.hash) return { seen: false, value: "" };
  const params = new URLSearchParams(window.location.hash.slice(1));
  const supplied = params.get("agentops-owner-setup");
  if (supplied === null) return { seen: false, value: "" };
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
  return {
    seen: true,
    value: /^[A-Za-z0-9_-]{16,256}$/.test(supplied) ? supplied : "",
  };
}

function consumePairingHandoff(): PairingHandoff {
  if (typeof window === "undefined" || !window.location.hash) return { seen: false, value: "" };
  const params = new URLSearchParams(window.location.hash.slice(1));
  const supplied = params.get("pair");
  if (supplied === null) return { seen: false, value: "" };
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}`);
  return {
    seen: true,
    value: supplied.length > 0 && supplied.length <= 1024 && !/\s/.test(supplied) ? supplied : "",
  };
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { locale } = usePreferences();
  const [gate, setGate] = useState<GateState>("checking");
  const [status, setStatus] = useState<HumanAuthStatus | null>(null);
  const [username, setUsername] = useState("");
  const [initialPairingHandoff] = useState(consumePairingHandoff);
  const [initialSetupHandoff] = useState(consumeOwnerSetupHandoff);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pairingSecret, setPairingSecret] = useState(initialPairingHandoff.value);
  const [pairingHandoffActive, setPairingHandoffActive] = useState(initialPairingHandoff.seen);
  const [setupCode, setSetupCode] = useState(initialSetupHandoff.value);
  const [setupHandoffActive, setSetupHandoffActive] = useState(Boolean(initialSetupHandoff.value));
  const [displayName, setDisplayName] = useState("");
  const [deviceLabel, setDeviceLabel] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(() => initialPairingHandoff.seen && !initialPairingHandoff.value
    ? authErrorMessage("invalid_pairing_secret", locale)
    : "");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [recoveryAuthority, setRecoveryAuthority] = useState("");

  const refresh = useCallback(async () => {
    setGate("checking");
    setError("");
    try {
      const next = await getHumanAuthStatus();
      setStatus(next);
      if (pairingHandoffActive) {
        setError(pairingSecret ? "" : authErrorMessage("invalid_pairing_secret", locale));
        setGate("pairing");
      }
      else if (!next.required || next.authenticated) setGate("ready");
      else setGate(next.bootstrap_required ? "bootstrap" : "login");
    } catch (nextError) {
      setError(authErrorMessage(errorCode(nextError), locale));
      setGate("unavailable");
    }
  }, [locale, pairingHandoffActive, pairingSecret]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const handleSetupHandoff = () => {
      const pairing = consumePairingHandoff();
      if (pairing.seen) {
        setSetupCode("");
        setSetupHandoffActive(false);
        setPairingSecret(pairing.value);
        setPairingHandoffActive(true);
        setGate("pairing");
        setError(pairing.value ? "" : authErrorMessage("invalid_pairing_secret", locale));
        return;
      }
      const next = consumeOwnerSetupHandoff();
      if (!next.seen) return;
      if (gate !== "checking" && gate !== "bootstrap" && gate !== "login") {
        setSetupCode("");
        setSetupHandoffActive(false);
        return;
      }
      setSetupCode(next.value);
      setSetupHandoffActive(Boolean(next.value));
      setPairingSecret("");
      setPairingHandoffActive(false);
      setError(next.value ? "" : authErrorMessage("invalid_setup_code", locale));
    };
    window.addEventListener("hashchange", handleSetupHandoff);
    return () => window.removeEventListener("hashchange", handleSetupHandoff);
  }, [gate, locale]);

  useEffect(() => {
    const handleUnauthorized = () => {
      setHumanAuthCsrf(null);
      setStatus((current) => current ? { ...current, authenticated: false, user: undefined } : current);
      setGate("login");
      setShowPassword(false);
      setShowConfirmPassword(false);
      setError(pick(locale, {
        zh: "登录已过期，请重新登录。",
        en: "Your session expired. Please sign in again.",
      }));
    };
    window.addEventListener(HUMAN_AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => window.removeEventListener(HUMAN_AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
  }, [locale]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if ((gate === "bootstrap" || gate === "recovery" || gate === "pairing") && password !== confirmPassword) {
      setError(pick(locale, { zh: "两次输入的密码不一致。", en: "The passwords do not match." }));
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const session = gate === "pairing"
        ? await pairHuman({
            pairing_secret: pairingSecret,
            username,
            password,
            display_name: displayName || undefined,
            device_label: deviceLabel || undefined,
          })
        : gate === "bootstrap"
          ? await bootstrapHuman({ setup_code: setupCode, username, password, display_name: displayName || undefined })
        : gate === "recovery"
          ? await completeHumanPasswordRecovery({ recovery_authority: recoveryAuthority, username, password })
          : await loginHuman({ username, password });
      setStatus({
        required: true,
        authenticated: true,
        bootstrap_required: false,
        password_recovery_available: true,
        password_recovery_local_only: true,
        user: session.user,
      });
      setPassword("");
      setConfirmPassword("");
      setPairingSecret("");
      setPairingHandoffActive(false);
      setSetupCode("");
      setSetupHandoffActive(false);
      setRecoveryAuthority("");
      setDeviceLabel("");
      setShowPassword(false);
      setShowConfirmPassword(false);
      setGate("ready");
    } catch (nextError) {
      const code = errorCode(nextError);
      if (gate === "bootstrap" && !["weak_password", "invalid_username"].includes(code)) {
        setSetupCode("");
        setSetupHandoffActive(false);
      }
      if (gate === "recovery" && code === "invalid_recovery_authority") {
        setRecoveryAuthority("");
        setGate("login");
      }
      setError(authErrorMessage(code, locale));
    } finally {
      setSubmitting(false);
    }
  };

  const beginRecovery = async () => {
    setSubmitting(true);
    setError("");
    try {
      const recovery = await startHumanPasswordRecovery(setupCode);
      setRecoveryAuthority(recovery.recovery_authority);
      setPassword("");
      setConfirmPassword("");
      setShowPassword(false);
      setShowConfirmPassword(false);
      setGate("recovery");
    } catch (nextError) {
      setError(authErrorMessage(errorCode(nextError), locale));
    } finally {
      setSubmitting(false);
    }
  };

  const returnToLogin = () => {
    setRecoveryAuthority("");
    setPassword("");
    setConfirmPassword("");
    setShowPassword(false);
    setShowConfirmPassword(false);
    setError("");
    setGate("login");
  };

  const leavePairing = () => {
    setPairingSecret("");
    setPairingHandoffActive(false);
    setPassword("");
    setConfirmPassword("");
    setDisplayName("");
    setDeviceLabel("");
    setShowPassword(false);
    setShowConfirmPassword(false);
    setError("");
    if (!status?.required || status?.authenticated) setGate("ready");
    else setGate(status.bootstrap_required ? "bootstrap" : "login");
  };

  const logout = useCallback(async () => {
    try {
      await logoutHuman();
    } finally {
      setHumanAuthCsrf(null);
      setStatus((current) => current ? { ...current, authenticated: false, user: undefined } : current);
      setPairingSecret("");
      setPairingHandoffActive(false);
      setShowPassword(false);
      setShowConfirmPassword(false);
      setGate("login");
    }
  }, []);

  const contextValue = useMemo(() => ({
    required: Boolean(status?.required),
    user: status?.user || null,
    logout,
  }), [logout, status]);

  if (gate === "ready") {
    return <HumanAuthContext.Provider value={contextValue}>{children}</HumanAuthContext.Provider>;
  }

  const isBootstrap = gate === "bootstrap";
  const isRecovery = gate === "recovery";
  const isPairing = gate === "pairing";
  const hasInstallerHandoff = isBootstrap && setupHandoffActive;
  const passwordLength = Array.from(password).length;
  const passwordReady = passwordLength >= 12;
  const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword;
  const usernameReady = /^[a-z0-9][a-z0-9._-]{2,63}$/.test(username);
  const bootstrapFormReady = Boolean(setupCode.trim()) && usernameReady && passwordReady && passwordsMatch;
  const recoveryFormReady = Boolean(recoveryAuthority) && usernameReady && passwordReady && passwordsMatch;
  const pairingFormReady = Boolean(pairingSecret) && usernameReady && passwordReady && passwordsMatch;
  const passwordHint = !isBootstrap && !isRecovery && !isPairing
    ? undefined
    : password.length === 0
      ? pick(locale, {
          zh: "至少 12 个字符，不要求大小写或符号组合",
          en: "At least 12 characters; no composition rules",
        })
      : passwordReady
        ? pick(locale, { zh: "长度已满足", en: "Length requirement met" })
        : pick(locale, {
            zh: `还需 ${12 - passwordLength} 个字符`,
            en: `${12 - passwordLength} more characters required`,
          });
  const confirmPasswordHint = confirmPassword.length === 0
    ? pick(locale, { zh: "再次输入相同密码", en: "Enter the same password again" })
    : passwordsMatch
      ? pick(locale, { zh: "两次输入一致", en: "Passwords match" })
      : pick(locale, { zh: "两次输入不一致", en: "Passwords do not match" });
  const togglePasswordLabel = showPassword
    ? pick(locale, { zh: "隐藏密码", en: "Hide password" })
    : pick(locale, { zh: "显示密码", en: "Show password" });
  const toggleConfirmPasswordLabel = showConfirmPassword
    ? pick(locale, { zh: "隐藏确认密码", en: "Hide confirmation password" })
    : pick(locale, { zh: "显示确认密码", en: "Show confirmation password" });
  const lockLabel = pick(locale, {
    zh: gate === "checking" ? "正在连接" : isBootstrap ? "需要初始化" : isRecovery ? "账户恢复" : isPairing ? "设备配对" : "需要登录",
    en: gate === "checking" ? "Connecting" : isBootstrap ? "Setup required" : isRecovery ? "Account recovery" : isPairing ? "Device pairing" : "Sign-in required",
  });
  const pageTitle = pick(locale, {
    zh: "账户与访问",
    en: "Account and access",
  });
  const pageSubtitle = pick(locale, {
    zh: isBootstrap ? "设置管理员后即可进入当前工作区" : isRecovery ? "仅可在安装 AgentOps MIS 的主机上完成" : isPairing ? "使用 Owner 发出的一次性邀请加入工作区" : "使用这台主机上的账户继续",
    en: isBootstrap ? "Set up the administrator to enter this workspace" : isRecovery ? "Complete recovery on the AgentOps MIS host" : isPairing ? "Join the workspace with a one-time Owner invitation" : "Continue with an account on this host",
  });

  return (
    <HumanAuthContext.Provider value={contextValue}>
      <AppShell locked lockLabel={lockLabel}>
        <WorkspaceSettingsPage
          testId="human-auth-workspace-gate"
          title={pageTitle}
          subtitle={pageSubtitle}
        >
          <WorkspaceSettingsSection
            testId="human-auth-settings-layout"
            title={pick(locale, {
              zh: isBootstrap ? "设置管理员账户" : isRecovery ? "重设密码" : isPairing ? "设置成员账户" : "登录",
              en: isBootstrap ? "Set up administrator" : isRecovery ? "Reset password" : isPairing ? "Set up member account" : "Sign in",
            })}
            description={pick(locale, {
              zh: isBootstrap
                ? "创建第一个管理员。之后可从本机登录，或用一次性邀请授权另一台电脑的浏览器。"
                : isRecovery
                  ? "设置新密码后，其他已登录设备会自动退出。"
                  : isPairing
                    ? "邀请决定成员角色；完成后此浏览器将绑定为该成员的设备。"
                  : "输入账户信息继续。",
              en: isBootstrap
                ? "Create the first administrator, then sign in locally or authorize another computer's browser with a one-time invitation."
                : isRecovery
                  ? "Other signed-in devices will be signed out after the password changes."
                  : isPairing
                    ? "The invitation sets the member role; this browser is bound as that member's device."
                  : "Enter your account details to continue.",
            })}
            meta={(
              <p data-testid="human-auth-host-boundary" className="mt-3 flex items-center gap-1.5 text-[11px]" style={{ color: "var(--mis-muted)" }}>
                <LockKeyhole size={11} aria-hidden="true" />
                {pick(locale, { zh: "账户与运行数据仅保留在主机", en: "Account and runtime data stay on the host" })}
              </p>
            )}
          >
            <div data-testid="human-auth-access-panel">
              {gate === "checking" ? (
                <div className="flex min-h-32 items-center gap-2.5 border-y text-xs" style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}>
                  <LoaderCircle className="animate-spin" size={17} aria-hidden="true" />
                  {pick(locale, { zh: "正在验证主机会话...", en: "Checking host session..." })}
                </div>
              ) : gate === "unavailable" ? (
                <div className="border-y py-5" style={{ borderColor: "var(--mis-border)" }}>
                  <h3 className="text-sm font-semibold">{pick(locale, { zh: "无法连接本地主机", en: "Cannot reach local host" })}</h3>
                  <p className="mt-2 text-xs leading-5" style={{ color: "var(--mis-dim)" }}>{error}</p>
                  <button
                    type="button"
                    onClick={() => void refresh()}
                    className="mt-4 inline-flex h-9 items-center justify-center gap-2 rounded px-3 text-xs font-semibold"
                    style={{ background: "var(--mis-cyan)", color: "var(--mis-bg)" }}
                  >
                    <RefreshCw size={15} aria-hidden="true" />
                    {pick(locale, { zh: "重新连接", en: "Retry connection" })}
                  </button>
                </div>
              ) : (
                <form onSubmit={submit} data-testid="human-auth-workspace-form">
                  <div className="divide-y border-y" style={{ borderColor: "var(--mis-border)" }}>
                    {isBootstrap && !hasInstallerHandoff && (
                      <details data-testid="human-auth-advanced-setup" className="py-3 text-xs">
                        <summary className="cursor-pointer font-medium" style={{ color: "var(--mis-dim)" }}>
                          {pick(locale, { zh: "无法继续？使用手动初始化", en: "Can't continue? Use manual setup" })}
                        </summary>
                        <div className="mt-2 border-t" style={{ borderColor: "var(--mis-border)" }}>
                          <AuthField
                            label={pick(locale, { zh: "初始化密钥", en: "Initialization key" })}
                            hint={pick(locale, { zh: "仅在应用未自动接入时使用", en: "Use only when the app did not connect automatically" })}
                            value={setupCode}
                            onChange={setSetupCode}
                            type="password"
                            autoComplete="one-time-code"
                          />
                        </div>
                      </details>
                    )}
                    {hasInstallerHandoff && (
                      <span data-testid="owner-setup-handoff-ready" className="sr-only">
                        {pick(locale, { zh: "初始化已就绪", en: "Setup is ready" })}
                      </span>
                    )}
                    {(isBootstrap || isPairing) && (
                      <AuthField
                        label={pick(locale, { zh: "显示名称", en: "Display name" })}
                        value={displayName}
                        onChange={setDisplayName}
                        autoComplete="name"
                        placeholder={pick(locale, { zh: "例如：Joy", en: "For example: Joy" })}
                        required={false}
                      />
                    )}
                    {isPairing && (
                      <AuthField
                        label={pick(locale, { zh: "设备名称", en: "Device label" })}
                        hint={pick(locale, { zh: "帮助 Owner 在设备列表中识别此浏览器", en: "Helps the Owner identify this browser in the device list" })}
                        value={deviceLabel}
                        onChange={setDeviceLabel}
                        autoComplete="off"
                        placeholder={pick(locale, { zh: "例如：工作 MacBook", en: "For example: Work MacBook" })}
                        required={false}
                      />
                    )}
                    <AuthField
                      label={pick(locale, { zh: "用户名", en: "Username" })}
                      hint={isBootstrap || isRecovery || isPairing ? pick(locale, { zh: "用于登录，可使用小写字母、数字和 . _ -", en: "Used to sign in; lowercase letters, digits, and . _ -" }) : undefined}
                      value={username}
                      onChange={setUsername}
                      autoComplete="username"
                      placeholder={isBootstrap ? "joy-owner" : undefined}
                      pattern={isBootstrap || isRecovery || isPairing ? "[a-z0-9][a-z0-9._\\-]{2,63}" : undefined}
                    />
                    <AuthField
                      label={pick(locale, { zh: "密码", en: "Password" })}
                      hint={passwordHint}
                      hintTone={password.length > 0 ? (passwordReady ? "success" : "warning") : "muted"}
                      value={password}
                      onChange={setPassword}
                      type={showPassword ? "text" : "password"}
                      autoComplete={isBootstrap || isRecovery || isPairing ? "new-password" : "current-password"}
                      minLength={isBootstrap || isRecovery || isPairing ? 12 : undefined}
                      revealControl={{
                        visible: showPassword,
                        label: togglePasswordLabel,
                        onToggle: () => setShowPassword((current) => !current),
                      }}
                    />
                    {(isBootstrap || isRecovery || isPairing) && (
                      <AuthField
                        label={pick(locale, { zh: "确认密码", en: "Confirm password" })}
                        hint={confirmPasswordHint}
                        hintTone={confirmPassword.length > 0 ? (passwordsMatch ? "success" : "warning") : "muted"}
                        value={confirmPassword}
                        onChange={setConfirmPassword}
                        type={showConfirmPassword ? "text" : "password"}
                        autoComplete="new-password"
                        minLength={12}
                        revealControl={{
                          visible: showConfirmPassword,
                          label: toggleConfirmPasswordLabel,
                          onToggle: () => setShowConfirmPassword((current) => !current),
                        }}
                      />
                    )}
                  </div>

                  {error && (
                    <div className="grid gap-1.5 border-b py-3 text-xs sm:grid-cols-[160px_minmax(0,1fr)] sm:gap-3" style={{ borderColor: "var(--mis-border)" }}>
                      <span className="hidden sm:block" aria-hidden="true" />
                      <div role="alert" className="max-w-md rounded px-3 py-2.5" style={{ background: "color-mix(in srgb, var(--mis-warning) 8%, transparent)", color: "var(--mis-warning)" }}>
                        {error}
                      </div>
                    </div>
                  )}

                  <div className="grid gap-3 pt-4 sm:grid-cols-[160px_minmax(0,1fr)]">
                    <span className="hidden sm:block" aria-hidden="true" />
                    <div className="flex max-w-md flex-wrap items-center gap-3">
                      <button
                        disabled={submitting || (isBootstrap && !bootstrapFormReady) || (isRecovery && !recoveryFormReady) || (isPairing && !pairingFormReady)}
                        className="inline-flex h-9 w-full items-center justify-center gap-2 rounded px-4 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto sm:min-w-40"
                        style={{ background: "var(--mis-primary)", color: "#fff" }}
                      >
                        {submitting ? <LoaderCircle className="animate-spin" size={16} aria-hidden="true" /> : <ArrowRight size={16} aria-hidden="true" />}
                        {pick(locale, {
                          zh: isBootstrap ? "创建管理员并进入" : isRecovery ? "重设密码并进入" : isPairing ? "完成配对并进入" : "登录",
                          en: isBootstrap ? "Create administrator" : isRecovery ? "Reset password" : isPairing ? "Pair and continue" : "Sign in",
                        })}
                      </button>
                      {gate === "login" && status?.password_recovery_available === true && (
                        <button
                          type="button"
                          onClick={() => void beginRecovery()}
                          disabled={submitting}
                          className="inline-flex h-9 items-center gap-1.5 px-1 text-xs font-medium disabled:opacity-50"
                          style={{ color: "var(--mis-primary)" }}
                        >
                          <KeyRound size={14} aria-hidden="true" />
                          {pick(locale, { zh: "忘记密码", en: "Forgot password" })}
                        </button>
                      )}
                      {isRecovery && (
                        <button
                          type="button"
                          onClick={returnToLogin}
                          disabled={submitting}
                          className="inline-flex h-9 items-center gap-1.5 px-1 text-xs font-medium disabled:opacity-50"
                          style={{ color: "var(--mis-dim)" }}
                        >
                          <ArrowLeft size={14} aria-hidden="true" />
                          {pick(locale, { zh: "返回登录", en: "Back to sign in" })}
                        </button>
                      )}
                      {isPairing && (
                        <button
                          type="button"
                          onClick={leavePairing}
                          disabled={submitting}
                          className="inline-flex h-9 items-center gap-1.5 px-1 text-xs font-medium disabled:opacity-50"
                          style={{ color: "var(--mis-dim)" }}
                        >
                          <ArrowLeft size={14} aria-hidden="true" />
                          {pick(locale, { zh: status?.authenticated ? "返回工作区" : "返回登录", en: status?.authenticated ? "Back to workspace" : "Back to sign in" })}
                        </button>
                      )}
                    </div>
                  </div>
                </form>
              )}
            </div>
          </WorkspaceSettingsSection>
        </WorkspaceSettingsPage>
      </AppShell>
    </HumanAuthContext.Provider>
  );
}

function AuthField({
  label,
  hint,
  hintTone = "muted",
  value,
  onChange,
  type = "text",
  autoComplete,
  required = true,
  minLength,
  pattern,
  placeholder,
  revealControl,
}: {
  label: string;
  hint?: string;
  hintTone?: "muted" | "success" | "warning";
  value: string;
  onChange: (value: string) => void;
  type?: string;
  autoComplete: string;
  required?: boolean;
  minLength?: number;
  pattern?: string;
  placeholder?: string;
  revealControl?: {
    visible: boolean;
    label: string;
    onToggle: () => void;
  };
}) {
  const inputId = useId();
  const hintColor = hintTone === "success"
    ? "var(--mis-success)"
    : hintTone === "warning"
      ? "var(--mis-warning)"
      : "var(--mis-muted)";

  return (
    <div className="grid gap-1.5 py-3 text-xs sm:grid-cols-[160px_minmax(0,1fr)] sm:items-center sm:gap-3">
      <span className="min-w-0">
        <label htmlFor={inputId} className="block font-medium" style={{ color: "var(--mis-text)" }}>{label}</label>
        {hint && <span aria-live="polite" className="mt-0.5 block text-[10px] font-normal" style={{ color: hintColor }}>{hint}</span>}
      </span>
      <div className="relative min-w-0 max-w-md">
        <input
          id={inputId}
          type={type}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          required={required}
          minLength={minLength}
          pattern={pattern}
          placeholder={placeholder}
          className={`h-9 w-full min-w-0 rounded border px-3 text-sm outline-none transition-colors focus:ring-2 ${revealControl ? "pr-10" : ""}`}
          style={{
            background: "var(--mis-surface)",
            borderColor: "var(--mis-border)",
            color: "var(--mis-text)",
            boxShadow: "0 0 0 0 color-mix(in srgb, var(--mis-cyan) 24%, transparent)",
          }}
        />
        {revealControl && (
          <button
            type="button"
            onClick={revealControl.onToggle}
            aria-label={revealControl.label}
            aria-pressed={revealControl.visible}
            title={revealControl.label}
            className="absolute right-1 top-1 inline-flex h-7 w-7 items-center justify-center rounded"
            style={{ color: "var(--mis-dim)" }}
          >
            {revealControl.visible ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
          </button>
        )}
      </div>
    </div>
  );
}

function authErrorMessage(code: string, locale: "zh" | "en"): string {
  const messages: Record<string, { zh: string; en: string }> = {
    invalid_setup_code: { zh: "初始化密钥无效，请从 AgentOps MIS 应用重新打开。", en: "The initialization key is invalid. Reopen the AgentOps MIS app." },
    bootstrap_unavailable: { zh: "这台主机已经完成初始化，请直接登录。", en: "This host is already initialized. Sign in instead." },
    invalid_username: { zh: "用户名需为 3–64 位小写字母、数字或 . _ -。", en: "Use 3–64 lowercase letters, digits, or . _ -." },
    weak_password: { zh: "请使用至少 12 个字符的长短语。", en: "Use a passphrase with at least 12 characters." },
    invalid_credentials: { zh: "用户名或密码不正确。", en: "The username or password is incorrect." },
    invalid_pairing_secret: { zh: "配对邀请无效，请向 Owner 获取新的邀请。", en: "This pairing invitation is invalid. Ask the Owner for a new invitation." },
    invalid_pairing_invitation: { zh: "配对邀请无效或已过期，请向 Owner 获取新的邀请。", en: "This pairing invitation is invalid or expired. Ask the Owner for a new invitation." },
    invalid_pairing_profile: { zh: "成员资料无效或用户名已存在，请检查后重试。", en: "The member profile is invalid or the username already exists. Review it and try again." },
    pairing_invitation_expired: { zh: "配对邀请已过期，请向 Owner 获取新的邀请。", en: "This pairing invitation expired. Ask the Owner for a new invitation." },
    pairing_invitation_revoked: { zh: "配对邀请已被撤销，请向 Owner 获取新的邀请。", en: "This pairing invitation was revoked. Ask the Owner for a new invitation." },
    pairing_invitation_used: { zh: "配对邀请已经使用，请向 Owner 获取新的邀请。", en: "This pairing invitation has already been used. Ask the Owner for a new invitation." },
    username_exists: { zh: "该用户名已存在，请使用其他用户名。", en: "That username already exists. Choose another username." },
    human_auth_request_failed: { zh: "配对请求未完成，请检查主机连接后重试。", en: "The pairing request did not complete. Check the host connection and retry." },
    local_recovery_required: { zh: "为了保护主机数据，请在安装 AgentOps MIS 的电脑上打开本地控制台后重试。", en: "For security, open the local Console on the AgentOps MIS host to reset the password." },
    invalid_recovery_authority: { zh: "恢复请求已失效，请重新点击“忘记密码”。", en: "The recovery request expired. Start password recovery again." },
    owner_not_initialized: { zh: "请先设置管理员账户。", en: "Set up the administrator account first." },
    local_recovery_authority_required: { zh: "请从这台电脑上的 AgentOps MIS 应用重新打开控制台，再点击“忘记密码”。", en: "Reopen the Console from the AgentOps MIS app on this computer, then select Forgot password." },
    origin_validation_failed: { zh: "当前访问来源未被这台主机信任。", en: "This browser origin is not trusted by the host." },
    owner_already_initialized: { zh: "这台主机已经完成初始化，请直接登录。", en: "This host is already initialized. Sign in instead." },
    rate_limited: { zh: "尝试次数过多，请稍后再试。", en: "Too many attempts. Try again later." },
    too_many_attempts: { zh: "尝试次数过多，请稍后再试。", en: "Too many attempts. Try again later." },
    unknown: { zh: "请求未完成，请检查主机连接后重试。", en: "The request did not complete. Check the host connection and retry." },
  };
  return messages[code]?.[locale] || messages.unknown[locale];
}
