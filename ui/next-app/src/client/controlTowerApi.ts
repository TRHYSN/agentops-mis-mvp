export type HumanMembership = {
  workspace_id: string;
  role: string;
};

export type HumanSession = {
  ok: true;
  authenticated: true;
  user: {
    user_id: string;
    name: string;
  };
  memberships: HumanMembership[];
  csrf_token: string;
  session_expires_at: string;
  idle_ttl_seconds: number;
};

export type DashboardMetrics = {
  workspace_id: string;
  agents_total: number;
  agents_running: number;
  tasks_completed_total: number;
  total_cost_usd: number;
  pending_approvals: number;
  failure_rate: number;
  task_status_distribution: Array<{
    status: string;
    count: number;
  }>;
  control_plane: string;
};

export type WorkspaceTask = {
  task_id: string;
  title: string;
  status: string;
  priority: string;
  risk_level: string;
  owner_agent_id: string | null;
  updated_at: string;
};

export type WorkspaceRun = {
  run_id: string;
  task_id: string;
  agent_id: string;
  runtime_type: string;
  status: string;
  started_at: string;
  duration_ms: number;
  cost_usd: number;
  cost_usd_exact: string;
  approval_required: boolean;
};

export type WorkspaceApproval = {
  approval_id: string;
  approval_kind: string;
  task_id: string;
  run_id: string;
  decision: string;
  runtime_type: string;
  requested_by_agent_id: string | null;
  created_at: string;
  expires_at: string | null;
  prepared_action: {
    action_type: string;
    target_resource: string | null;
    risk_level: string;
  } | null;
};

export type ControlTowerSnapshot = {
  metrics: DashboardMetrics;
  tasks: WorkspaceTask[];
  runs: WorkspaceRun[];
  approvals: WorkspaceApproval[];
};

type ErrorPayload = {
  error?: unknown;
  message?: unknown;
};

export class ControlTowerApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ControlTowerApiError";
    this.status = status;
    this.code = code;
  }
}

async function responsePayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ControlTowerApiError(
      response.status || 502,
      "invalid_control_plane_response",
      "控制平面返回了无法读取的响应。",
    );
  }
}

export function createControlTowerClient(fetcher: typeof fetch = fetch) {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetcher(path, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...init?.headers,
      },
    });
    const payload = await responsePayload(response);
    if (!response.ok) {
      const failure = (payload && typeof payload === "object"
        ? payload
        : {}) as ErrorPayload;
      throw new ControlTowerApiError(
        response.status,
        String(failure.error || "control_plane_request_failed"),
        String(failure.message || "控制平面请求失败。"),
      );
    }
    return payload as T;
  }

  return {
    session() {
      return request<HumanSession>("/api/mis/human-auth/session");
    },
    login(username: string, password: string) {
      return request<HumanSession>("/api/mis/human-auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
    },
    logout(csrfToken: string) {
      return request<{ ok: true; authenticated: false }>(
        "/api/mis/human-auth/logout",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-AgentOps-CSRF": csrfToken,
          },
          body: "{}",
        },
      );
    },
    async workspaceSnapshot(workspaceId: string): Promise<ControlTowerSnapshot> {
      const query = new URLSearchParams({ workspace_id: workspaceId });
      const listQuery = new URLSearchParams({
        workspace_id: workspaceId,
        limit: "20",
      });
      const [metrics, tasks, runs, approvals] = await Promise.all([
        request<DashboardMetrics>(`/api/mis/dashboard/metrics?${query}`),
        request<WorkspaceTask[]>(`/api/mis/tasks?${listQuery}`),
        request<WorkspaceRun[]>(`/api/mis/runs?${listQuery}`),
        request<WorkspaceApproval[]>(`/api/mis/approvals?${listQuery}`),
      ]);
      return { metrics, tasks, runs, approvals };
    },
  };
}
