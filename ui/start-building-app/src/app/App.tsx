import { BrowserRouter, Routes, Route, Navigate } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { WorkspaceHome } from "./components/pages/WorkspaceHome";
import { PixelOffice } from "./components/pages/PixelOffice";
import { MyTasks } from "./components/pages/MyTasks";
import { AIEmployees } from "./components/pages/AIEmployees";
import { WorkerConsole } from "./components/pages/WorkerConsole";
import { CodexConnection } from "./components/pages/CodexConnection";
import { CustomerDispatchDesk } from "./components/pages/CustomerDispatchDesk";
import { ApprovalsInbox } from "./components/pages/ApprovalsInbox";
import { MemoryLibrary } from "./components/pages/MemoryLibrary";
import { Reports } from "./components/pages/Reports";
import { ControlTower } from "./components/pages/ControlTower";
import { EvaluationRoom } from "./components/pages/EvaluationRoom";
import { AgentDetail } from "./components/pages/AgentDetail";
import { TaskDetail } from "./components/pages/TaskDetail";
import { RunDetail } from "./components/pages/RunDetail";
import { RunLedger } from "./components/pages/RunLedger";
import { ToolCallLedger } from "./components/pages/ToolCallLedger";
import { RuntimeConnectors } from "./components/pages/RuntimeConnectors";
import { NotionBase } from "./components/pages/NotionBase";
import { TemplateSwitching } from "./components/pages/TemplateSwitching";
import { AuditCenter } from "./components/pages/AuditCenter";
import { CustomerProjectReport } from "./components/pages/CustomerProjectReport";
import { PrivateHostAcceptance } from "./components/pages/PrivateHostAcceptance";
import { AccountSecurity } from "./components/pages/AccountSecurity";
import { ExperimentDetail } from "./components/pages/ExperimentDetail";
import { Experiments } from "./components/pages/Experiments";
import { PreferencesProvider } from "./context/PreferencesContext";
import { AuthGate } from "./components/auth/AuthGate";
import { HUMAN_SESSION_REQUIRED } from "./data/liveApi";

export default function App() {
  return (
    <PreferencesProvider>
      <BrowserRouter>
        <AuthGate>
          <AppShell>
            <Routes>
              <Route path="/" element={<Navigate to="/workspace" replace />} />
              <Route path="/workspace" element={<WorkspaceHome />} />
              <Route path="/workspace/pixel-office" element={<PixelOffice />} />
              <Route path="/workspace/tasks" element={<MyTasks />} />
              <Route path="/workspace/agents" element={<AIEmployees />} />
              <Route path="/workspace/dispatch" element={<CustomerDispatchDesk />} />
              <Route path="/workspace/workers" element={<WorkerConsole />} />
              <Route path="/workspace/approvals" element={<ApprovalsInbox />} />
              <Route path="/workspace/memory" element={<MemoryLibrary />} />
              <Route path="/workspace/experiments" element={<Experiments />} />
              <Route path="/workspace/experiments/:id" element={<ExperimentDetail />} />
              <Route path="/workspace/reports" element={<Reports />} />
              <Route path="/workspace/account" element={<AccountSecurity />} />
              <Route path="/workspace/customer-projects/:projectId/report" element={<CustomerProjectReport />} />
              <Route path="/admin" element={<ControlTower />} />
              <Route path="/admin/codex" element={<Navigate to="/admin/connectors/codex" replace />} />
              <Route path="/admin/evaluations" element={<EvaluationRoom />} />
              <Route path="/admin/agents/:id" element={<AgentDetail />} />
              <Route path="/admin/tasks/:id" element={<TaskDetail />} />
              <Route path="/admin/runs" element={<RunLedger />} />
              <Route path="/admin/runs/:id" element={<RunDetail />} />
              <Route path="/admin/toolcalls" element={<ToolCallLedger />} />
              <Route path="/admin/connectors" element={<RuntimeConnectors />} />
              <Route path="/admin/connectors/codex" element={<CodexConnection />} />
              <Route path="/admin/bases/notion" element={<NotionBase />} />
              <Route path="/admin/templates" element={<TemplateSwitching />} />
              <Route path="/admin/audit" element={<AuditCenter />} />
              {!HUMAN_SESSION_REQUIRED && (
                <Route path="/admin/private-host-acceptance" element={<PrivateHostAcceptance />} />
              )}
            </Routes>
          </AppShell>
        </AuthGate>
      </BrowserRouter>
    </PreferencesProvider>
  );
}
