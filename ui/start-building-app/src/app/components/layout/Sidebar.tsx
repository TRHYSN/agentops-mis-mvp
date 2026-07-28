import { useState } from "react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router";
import {
  Home, CheckSquare, Bot, ShieldCheck, Brain, BarChart2, Package,
  Activity, List, Wrench, Plug, Database, ClipboardList, Map,
  ChevronDown, ChevronRight, Zap, TerminalSquare, ClipboardCheck, MonitorCheck, UserRound,
  Code2,
} from "lucide-react";
import { pick, usePreferences } from "../../context/PreferencesContext";
import { useHumanAuth } from "../../context/HumanAuthContext";

interface NavItem {
  labelKey: string;
  path: string;
  icon: ReactNode;
}

interface NavGroup {
  titleKey: string;
  items: NavItem[];
}

const navGroups: NavGroup[] = [
  {
    titleKey: "clientWorkspace",
    items: [
      { labelKey: "home",         path: "/workspace",              icon: <Home size={15} /> },
      { labelKey: "pixelOffice",  path: "/workspace/pixel-office", icon: <Map size={15} /> },
      { labelKey: "dispatchDesk", path: "/workspace/dispatch",     icon: <ClipboardCheck size={15} /> },
      { labelKey: "myTasks",      path: "/workspace/tasks",        icon: <CheckSquare size={15} /> },
      { labelKey: "aiEmployees",  path: "/workspace/agents",       icon: <Bot size={15} /> },
      { labelKey: "workerConsole",path: "/workspace/workers",      icon: <TerminalSquare size={15} /> },
      { labelKey: "approvals",    path: "/workspace/approvals",    icon: <ShieldCheck size={15} /> },
      { labelKey: "memory",       path: "/workspace/memory",       icon: <Brain size={15} /> },
      { labelKey: "reports",      path: "/workspace/reports",      icon: <BarChart2 size={15} /> },
      { labelKey: "account",      path: "/workspace/account",      icon: <UserRound size={15} /> },
      { labelKey: "templates",    path: "/admin/templates",        icon: <Package size={15} /> },
    ],
  },
  {
    titleKey: "adminConsole",
    items: [
      { labelKey: "controlTower",  path: "/admin",                    icon: <Activity size={15} /> },
      { labelKey: "codexConnection", path: "/admin/codex",            icon: <Code2 size={15} /> },
      { labelKey: "agentRegistry", path: "/workspace/agents",         icon: <Bot size={15} /> },
      { labelKey: "runLedger",     path: "/admin/runs",               icon: <List size={15} /> },
      { labelKey: "evaluationRoom",path: "/admin/evaluations",        icon: <BarChart2 size={15} /> },
      { labelKey: "toolCalls",     path: "/admin/toolcalls",          icon: <Wrench size={15} /> },
      { labelKey: "connectors",    path: "/admin/connectors",         icon: <Plug size={15} /> },
      { labelKey: "externalBases", path: "/admin/bases/notion",       icon: <Database size={15} /> },
      { labelKey: "audit",         path: "/admin/audit",              icon: <ClipboardList size={15} /> },
      { labelKey: "privateHostAcceptance", path: "/admin/private-host-acceptance", icon: <MonitorCheck size={15} /> },
    ],
  },
];

export function Sidebar({ locked = false }: { locked?: boolean }) {
  const location = useLocation();
  const { locale } = usePreferences();
  const { required: humanAuthRequired, user } = useHumanAuth();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const copy = pick(locale, {
    en: {
      clientWorkspace: "Client Workspace",
      adminConsole: "Admin Console",
      home: "Home",
      pixelOffice: "Pixel Office",
      dispatchDesk: "Dispatch Desk",
      myTasks: "My Tasks",
      aiEmployees: "AI Employees",
      workerConsole: "Worker Console",
      approvals: "Approvals",
      memory: "Memory",
      reports: "Reports",
      account: "Account and access",
      templates: "Templates",
      controlTower: "Control Tower",
      codexConnection: "Codex Connection",
      agentRegistry: "Agent Registry",
      runLedger: "Run Ledger",
      evaluationRoom: "Evaluation Room",
      toolCalls: "Tool Calls",
      connectors: "Connectors",
      externalBases: "External Bases",
      audit: "Audit",
      privateHostAcceptance: "Private Host Acceptance",
      workspace: "Workspace",
      productMode: "Private Host",
      lockedNavigation: "Sign in to access workspace data",
    },
    zh: {
      clientWorkspace: "前台工作区",
      adminConsole: "后台管理端",
      home: "首页",
      pixelOffice: "像素办公室",
      dispatchDesk: "派活台",
      myTasks: "我的任务",
      aiEmployees: "AI 员工",
      workerConsole: "Worker 控制台",
      approvals: "审批",
      memory: "记忆",
      reports: "报告",
      account: "账户与访问",
      templates: "模板",
      controlTower: "控制塔",
      codexConnection: "Codex 接入",
      agentRegistry: "代理注册表",
      runLedger: "运行账本",
      evaluationRoom: "评估室",
      toolCalls: "工具调用",
      connectors: "连接器",
      externalBases: "外部知识库",
      audit: "审计",
      privateHostAcceptance: "私有主机验收",
      workspace: "工作区",
      productMode: "私有主机",
      lockedNavigation: "登录后访问工作区数据",
    },
  });

  const toggleGroup = (title: string) => {
    setCollapsed((prev) => ({ ...prev, [title]: !prev[title] }));
  };
  const workspaceLabel = user?.workspace_id || (humanAuthRequired ? copy.productMode : "AgentOps Demo");
  const accountLabel = user?.display_name || user?.username || (humanAuthRequired ? copy.account : "jiwu@agentops.dev");

  return (
    <aside
      className={`${locked ? "hidden lg:flex" : "flex"} flex-col w-56 shrink-0 h-full border-r`}
      style={{
        background: "var(--mis-surface)",
        borderColor: "var(--mis-border)",
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center gap-2 px-4 py-4 border-b"
        style={{ borderColor: "var(--mis-border)" }}
      >
        <div
          className="w-7 h-7 rounded flex items-center justify-center"
          style={{ background: "var(--mis-cyan)", color: "#0B1020" }}
        >
          <Zap size={14} />
        </div>
        <div>
          <div className="text-xs font-semibold leading-tight" style={{ color: "var(--mis-text)" }}>
            AgentOps MIS
          </div>
          <div className="text-[10px] leading-tight" style={{ color: "var(--mis-dim)" }}>
            {copy.productMode}
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-4">
        {navGroups.map((group) => {
          const groupTitle = copy[group.titleKey as keyof typeof copy];
          const isCollapsed = collapsed[group.titleKey];
          return (
            <div key={group.titleKey}>
              <button
                onClick={() => toggleGroup(group.titleKey)}
                className="flex items-center justify-between w-full px-2 py-1 rounded text-[10px] font-semibold tracking-wider uppercase mb-1 hover:opacity-80 transition-opacity"
                style={{ color: "var(--mis-muted)" }}
              >
                {groupTitle}
                {isCollapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
              </button>

              {!isCollapsed && (
                <ul className="space-y-0.5">
                  {group.items.map((item) => {
                    const isActive = locked
                      ? item.path === "/workspace/account"
                      : location.pathname === item.path ||
                        (item.path !== "/workspace" && item.path !== "/admin" && location.pathname.startsWith(item.path));
                    const content = (
                      <>
                        {item.icon}
                        {copy[item.labelKey as keyof typeof copy]}
                      </>
                    );
                    return (
                      <li key={item.path}>
                        {locked ? (
                          <div
                            aria-disabled="true"
                            aria-current={isActive ? "page" : undefined}
                            className="flex items-center gap-2 px-2 py-1.5 rounded text-xs"
                            style={{
                              color: isActive ? "var(--mis-primary)" : "var(--mis-dim)",
                              background: isActive ? "color-mix(in srgb, var(--mis-primary) 8%, transparent)" : "transparent",
                            }}
                          >
                            {content}
                          </div>
                        ) : (
                          <NavLink
                            to={item.path}
                            className="flex items-center gap-2 px-2 py-1.5 rounded text-xs transition-colors"
                            style={{
                              color: isActive ? "var(--mis-cyan)" : "var(--mis-dim)",
                              background: isActive ? "rgba(34,211,238,0.08)" : "transparent",
                            }}
                            onMouseEnter={(e) => {
                              if (!isActive) (e.currentTarget as HTMLElement).style.color = "var(--mis-text)";
                            }}
                            onMouseLeave={(e) => {
                              if (!isActive) (e.currentTarget as HTMLElement).style.color = "var(--mis-dim)";
                            }}
                          >
                            {content}
                          </NavLink>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </nav>

      {/* Footer */}
      <div
        className="px-4 py-3 border-t text-[10px]"
        style={{ borderColor: "var(--mis-border)", color: "var(--mis-dim)" }}
      >
        {locked ? (
          <>
            <div>{copy.productMode}</div>
            <div style={{ color: "var(--mis-muted)" }}>{copy.lockedNavigation}</div>
          </>
        ) : (
          <>
            <div>{copy.workspace}: {workspaceLabel}</div>
            <div style={{ color: "var(--mis-muted)" }}>{accountLabel}</div>
          </>
        )}
      </div>
    </aside>
  );
}
