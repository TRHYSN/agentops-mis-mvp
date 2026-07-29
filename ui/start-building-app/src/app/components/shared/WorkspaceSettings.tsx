import type { ReactNode } from "react";

export function WorkspaceSettingsPage({
  title,
  subtitle,
  status,
  children,
  testId,
}: {
  title: string;
  subtitle: string;
  status?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-6" data-testid={testId}>
      <header className="flex flex-wrap items-start justify-between gap-3 border-b pb-4" style={{ borderColor: "var(--mis-border)" }}>
        <div className="min-w-0">
          <h1 className="text-lg font-semibold" style={{ color: "var(--mis-text)" }}>{title}</h1>
          <p className="mt-1 text-xs" style={{ color: "var(--mis-dim)" }}>{subtitle}</p>
        </div>
        {status}
      </header>
      {children}
    </div>
  );
}

export function WorkspaceSettingsSection({
  title,
  description,
  meta,
  children,
  testId,
}: {
  title: string;
  description: string;
  meta?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-10"
      data-testid={testId}
    >
      <div className="min-w-0">
        <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{title}</h2>
        <p className="mt-1.5 text-xs leading-5" style={{ color: "var(--mis-dim)" }}>{description}</p>
        {meta}
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}
