import { Search, Bell, ChevronDown, Radio, Moon, Sun, Languages, Palette, LogOut, LockKeyhole, Zap } from "lucide-react";
import { Link } from "react-router";
import { pick, usePreferences } from "../../context/PreferencesContext";
import type { ThemeMode } from "../../context/PreferencesContext";
import { useHumanAuth } from "../../context/HumanAuthContext";

const themeOrder: ThemeMode[] = ["enterprise", "ops", "workforce"];

export function Topbar({ locked = false, lockLabel }: { locked?: boolean; lockLabel?: string }) {
  const { theme, locale, setTheme, setLocale } = usePreferences();
  const { required: humanAuthRequired, user, logout } = useHumanAuth();
  const copy = pick(locale, {
    en: {
      workspace: "Workspace",
      workspaceName: "AgentOps Demo",
      privateHost: "Private Host",
      search: "Search agents, tasks, runs...",
      live: "Live",
      themeLabel: theme === "enterprise" ? "Enterprise" : theme === "ops" ? "Ops" : "Workforce",
      languageLabel: "EN",
      switchTheme: "Switch visual style",
      switchLanguage: "Switch language",
      logout: "Sign out",
      account: "Account and access",
      locked: "Locked",
      setup: "Account setup",
    },
    zh: {
      workspace: "工作区",
      workspaceName: "AgentOps 演示",
      privateHost: "本地主机",
      search: "搜索代理、任务、运行...",
      live: "实时",
      themeLabel: theme === "enterprise" ? "企业版" : theme === "ops" ? "控制面" : "员工 OS",
      languageLabel: "中",
      switchTheme: "切换视觉风格",
      switchLanguage: "切换语言",
      logout: "退出登录",
      account: "账户与访问",
      locked: "已锁定",
      setup: "账户设置",
    },
  });

  const cycleTheme = () => {
    const nextIndex = (themeOrder.indexOf(theme) + 1) % themeOrder.length;
    setTheme(themeOrder[nextIndex]);
  };
  const displayName = user?.display_name || user?.username || "Jiwu Wang";
  const workspaceName = locked ? copy.privateHost : user?.workspace_id || copy.workspaceName;
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "JW";

  return (
    <header
      className="flex items-center justify-between gap-4 px-4 lg:px-5 h-12 shrink-0 border-b"
      style={{
        background: "var(--mis-surface)",
        borderColor: "var(--mis-border)",
      }}
    >
      <div className="flex min-w-0 items-center gap-2 md:hidden">
        <span
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded"
          style={{ background: "var(--mis-cyan)", color: "var(--mis-bg)" }}
        >
          <Zap size={13} aria-hidden="true" />
        </span>
        <span className="truncate text-xs font-semibold" style={{ color: "var(--mis-text)" }}>AgentOps MIS</span>
      </div>

      {/* Left: workspace switcher */}
      {locked ? (
        <div className="hidden min-w-0 items-center gap-2 text-xs md:flex" style={{ color: "var(--mis-dim)" }}>
          <span className="font-medium" style={{ color: "var(--mis-text)" }}>{workspaceName}</span>
          <span aria-hidden="true">/</span>
          <span>{copy.setup}</span>
        </div>
      ) : (
        <button
          className="hidden items-center gap-1.5 text-xs rounded px-2 py-1 hover:opacity-80 transition-opacity shrink-0 md:flex"
          style={{ color: "var(--mis-text)", background: "var(--mis-surface2)" }}
        >
          <span style={{ color: "var(--mis-dim)" }}>{copy.workspace}:</span>
          {workspaceName}
          <ChevronDown size={12} style={{ color: "var(--mis-dim)" }} />
        </button>
      )}

      {/* Center: search */}
      {locked ? (
        <div className="hidden flex-1 md:block" />
      ) : (
        <div
          className="hidden md:flex items-center gap-2 px-3 py-1.5 rounded text-xs w-full max-w-xl"
          style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)" }}
        >
          <Search size={13} />
          <span>{copy.search}</span>
        </div>
      )}

      {/* Right */}
      <div className="flex items-center gap-2 shrink-0">
        {/* Live mode badge */}
        <div
          className="flex items-center gap-1.5 px-1 text-[11px] font-medium"
          style={{
            color: locked ? "var(--mis-warning)" : "var(--mis-success)",
          }}
        >
          {locked ? <LockKeyhole size={11} /> : <Radio size={11} className="animate-pulse" />}
          {locked ? (lockLabel || copy.locked) : copy.live}
        </div>

        <button
          className="h-7 px-2 rounded flex items-center gap-1.5 text-[11px] hover:opacity-80"
          style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
          onClick={cycleTheme}
          title={copy.switchTheme}
        >
          {theme === "enterprise" ? <Sun size={13} /> : theme === "ops" ? <Moon size={13} /> : <Palette size={13} />}
          <span className="hidden lg:inline">{copy.themeLabel}</span>
        </button>

        <button
          className="h-7 px-2 rounded flex items-center gap-1.5 text-[11px] hover:opacity-80"
          style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
          onClick={() => setLocale(locale === "en" ? "zh" : "en")}
          title={copy.switchLanguage}
        >
          <Languages size={13} />
          {copy.languageLabel}
        </button>

        {/* Notifications */}
        {!locked && (
          <button
            className="relative p-1.5 rounded hover:opacity-80"
            style={{ color: "var(--mis-dim)" }}
          >
            <Bell size={15} />
            <span
              className="absolute top-0.5 right-0.5 w-2 h-2 rounded-full"
              style={{ background: "var(--mis-warning)" }}
            />
          </button>
        )}

        {/* Avatar */}
        {!locked && humanAuthRequired ? (
          <Link
            to="/workspace/account"
            className="flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold"
            style={{ background: "var(--mis-purple)", color: "#fff" }}
            title={copy.account}
            aria-label={copy.account}
          >
            {initials}
          </Link>
        ) : !locked ? (
          <div
            className="flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold"
            style={{ background: "var(--mis-purple)", color: "#fff" }}
            title={displayName}
          >
            {initials}
          </div>
        ) : null}
        {humanAuthRequired && !locked && (
          <button
            type="button"
            className="h-7 w-7 rounded flex items-center justify-center hover:opacity-80"
            style={{ background: "var(--mis-surface2)", color: "var(--mis-dim)", border: "1px solid var(--mis-border)" }}
            onClick={() => void logout()}
            title={copy.logout}
            aria-label={copy.logout}
          >
            <LogOut size={13} />
          </button>
        )}
      </div>
    </header>
  );
}
