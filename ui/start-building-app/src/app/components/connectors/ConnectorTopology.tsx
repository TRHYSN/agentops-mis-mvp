import type { ReactNode } from "react";
import { ArrowDown, ArrowRight, ExternalLink } from "lucide-react";
import { Link } from "react-router";
import { StatusBadge } from "../shared/StatusBadge";

export interface ConnectorTopologyNode {
  id: string;
  label: string;
  value: string;
  detail: string;
  status: string;
  icon: ReactNode;
  to?: string;
}

interface ConnectorTopologyProps {
  label: string;
  description: string;
  nodes: ConnectorTopologyNode[];
}

function NodeBody({ node }: { node: ConnectorTopologyNode }) {
  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded"
          style={{ color: "var(--mis-cyan)", background: "var(--mis-surface2)" }}
        >
          {node.icon}
        </span>
        <StatusBadge status={node.status} />
      </div>
      <div className="mt-3 min-w-0">
        <div className="text-[10px] font-medium" style={{ color: "var(--mis-muted)" }}>{node.label}</div>
        <div className="mt-0.5 truncate text-xs font-semibold" title={node.value} style={{ color: "var(--mis-text)" }}>
          {node.value}
        </div>
        <div className="mt-1 line-clamp-2 min-h-8 text-[10px] leading-4" style={{ color: "var(--mis-dim)" }}>
          {node.detail}
        </div>
      </div>
      {node.to && (
        <ExternalLink size={11} className="absolute bottom-3 right-3" style={{ color: "var(--mis-muted)" }} />
      )}
    </>
  );
}

export function ConnectorTopology({ label, description, nodes }: ConnectorTopologyProps) {
  return (
    <section className="py-1">
      <div className="mb-3 flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
        <h2 className="text-sm font-semibold" style={{ color: "var(--mis-text)" }}>{label}</h2>
        <p className="max-w-3xl text-[11px] md:text-right" style={{ color: "var(--mis-muted)" }}>{description}</p>
      </div>
      <div className="flex flex-col lg:flex-row lg:items-stretch">
        {nodes.map((node, index) => {
          const content = (
            <div
              className="relative min-h-28 min-w-0 flex-1 rounded p-3 transition-colors"
              style={{ background: "var(--mis-surface)", border: "1px solid var(--mis-border)" }}
            >
              <NodeBody node={node} />
            </div>
          );
          return (
            <div key={node.id} className="contents">
              {node.to ? (
                <Link to={node.to} className="min-w-0 flex-1 focus:outline-none focus-visible:ring-2" style={{ borderRadius: 6 }}>
                  {content}
                </Link>
              ) : content}
              {index < nodes.length - 1 && (
                <div className="flex h-8 shrink-0 items-center justify-center lg:h-auto lg:w-9">
                  <ArrowDown size={15} className="lg:hidden" style={{ color: "var(--mis-muted)" }} />
                  <ArrowRight size={15} className="hidden lg:block" style={{ color: "var(--mis-muted)" }} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
