import { Outlet } from "react-router";
import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { usePreferences } from "../../context/PreferencesContext";

export function AppShell({
  children,
  locked = false,
  lockLabel,
}: {
  children?: ReactNode;
  locked?: boolean;
  lockLabel?: string;
}) {
  const { theme } = usePreferences();

  return (
    <div
      className={`app-shell theme-${theme} flex h-screen w-screen overflow-hidden`}
      style={{ background: "var(--mis-bg)", color: "var(--mis-text)" }}
    >
      <Sidebar locked={locked} />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Topbar locked={locked} lockLabel={lockLabel} />
        <main
          data-testid={locked ? "locked-workspace-main" : "workspace-main"}
          className={`app-main ${locked ? "app-main-locked" : ""} flex-1 overflow-y-auto p-4 lg:p-5`}
        >
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
}
