import { Navigate, Route, Routes } from "react-router";
import { ReliabilityAgents } from "./ReliabilityAgents";
import { ReliabilityCampaignDetail } from "./ReliabilityCampaignDetail";
import { ReliabilityCampaigns } from "./ReliabilityCampaigns";
import { ReliabilityFailures } from "./ReliabilityFailures";
import { ReliabilityOverview } from "./ReliabilityOverview";
import { ReliabilityRegressions } from "./ReliabilityRegressions";
import { ReliabilityReleaseGates } from "./ReliabilityReleaseGates";
import { ReliabilityRunDetail } from "./ReliabilityRunDetail";
import { ReliabilityScenarioSuites } from "./ReliabilityScenarioSuites";

export default function ReliabilityLabRoutes() {
  return (
    <Routes>
      <Route index element={<ReliabilityOverview />} />
      <Route path="agents" element={<ReliabilityAgents />} />
      <Route path="scenario-suites" element={<ReliabilityScenarioSuites />} />
      <Route path="campaigns" element={<ReliabilityCampaigns />} />
      <Route path="campaigns/:id" element={<ReliabilityCampaignDetail />} />
      <Route path="runs/:id" element={<ReliabilityRunDetail />} />
      <Route path="failures" element={<ReliabilityFailures />} />
      <Route path="regressions" element={<ReliabilityRegressions />} />
      <Route path="release-gates" element={<ReliabilityReleaseGates />} />
      <Route path="*" element={<Navigate to="." replace />} />
    </Routes>
  );
}
