export type MigrationDefinition = Readonly<{
  component: string;
  version: string;
  schemaContract: string;
  filename: string;
  checksum: string;
}>;

export const SCHEMA_CONTRACT = "agentops_commercial_postgres_v11";

export const EXPECTED_POSTGRES_SCHEMA_FINGERPRINT = Object.freeze({
  contract: "agentops_postgres_schema_fingerprint_v1",
  sha256: "cf866b5de9f29eda409e3a98fa1942f9ce15b42e1d75f16a1238b41c47b2d1b3",
  objectCount: 861,
});

export const POSTGRES_MIGRATION_MANIFEST = Object.freeze([
  {
    component: "commercial_control_plane_baseline",
    version: "20260724.1",
    schemaContract: "current_main_commercial_baseline_v1",
    filename: "20260724_current_main_commercial_baseline.sql",
    checksum: "da149c133e6d731c446650fa1dc50973e54e5a9b786369b0db5af4d7951ae068",
  },
  {
    component: "human_session_memory_review",
    version: "20260718.1",
    schemaContract: "human_session_memory_review_v1",
    filename: "20260718_human_session_memory_review.sql",
    checksum: "8b9e121ce6615fb9475b494b534cad6e1fca84bb9c01b70fe896bc87debe196f",
  },
  {
    component: "workspace_read_models",
    version: "20260719.2",
    schemaContract: "workspace_read_models_v2",
    filename: "20260719_workspace_read_models_v2.sql",
    checksum: "cd2115869682fe44d37b00344023446d001a4cf8df034f5ae0a1044584659e0a",
  },
  {
    component: "human_approval_decisions",
    version: "20260719.3",
    schemaContract: "human_approval_decisions_v3",
    filename: "20260719_human_approval_decisions_v3.sql",
    checksum: "bb88014418b69754908dcaeccdca88e37b0819f938053f11ddf9a7e9cba32124",
  },
  {
    component: "approval_kind_bindings",
    version: "20260719.4",
    schemaContract: "approval_kind_bindings_v4",
    filename: "20260719_approval_kind_bindings_v4.sql",
    checksum: "c68d80f35a1b58943d3dc489b5fa5135a9b9f5042723a5146facabc8ccafffaf",
  },
  {
    component: "customer_delivery_run_unique",
    version: "20260724.5",
    schemaContract: "customer_delivery_run_unique_v5",
    filename: "20260724_customer_delivery_run_unique_v5.sql",
    checksum: "bd1ab7a550a9ab135c4058113a63dc621f1ae1558fa1060d6a4deed3cfd5a284",
  },
  {
    component: "prepared_action_execution_leases",
    version: "20260724.6",
    schemaContract: "prepared_action_execution_leases_v6",
    filename: "20260724_prepared_action_execution_leases_v6.sql",
    checksum: "4165407bbe609f1c30cf7a420e4efb8cfd5f789059645caa0b6b92ffff8bec1d",
  },
  {
    component: "governed_knowledge_index",
    version: "20260724.7",
    schemaContract: "governed_knowledge_index_v7",
    filename: "20260724_governed_knowledge_index_v7.sql",
    checksum: "ea0c543d7a1151d52e8262afc1141fd60f9b7520d5efff5783a82b7335b4bb56",
  },
  {
    component: "worker_evidence_workspace",
    version: "20260724.8",
    schemaContract: "worker_evidence_workspace_v8",
    filename: "20260724_worker_evidence_workspace_v8.sql",
    checksum: "ad5c15c636f15395d71a614478acbcdf7361604156362bb8c1f21d4c34b03d11",
  },
  {
    component: "workspace_entitlements",
    version: "20260724.9",
    schemaContract: "workspace_entitlements_v9",
    filename: "20260724_workspace_entitlements_v9.sql",
    checksum: "a22dc35565b5ae39ff553567154a80c4168957cdfecf5393d82adb3bad032419",
  },
  {
    component: "workspace_membership_roles",
    version: "20260731.10",
    schemaContract: "workspace_membership_roles_v10",
    filename: "20260731_workspace_membership_roles_v10.sql",
    checksum: "7a187c43c27bcb9a660e060fc45b2ccc36d3db04f8ffc4fb31fd58615ba71486",
  },
  {
    component: "run_cost_reservations",
    version: "20260731.10.1",
    schemaContract: "run_cost_reservations_v10",
    filename: "20260731_cost_reservations_v10.sql",
    checksum: "ea79487822c561841ed13239db41cc256efe63b3fa54f4973333d48cd60d4c85",
  },
  {
    component: "entitlement_admin_challenges",
    version: "20260731.11",
    schemaContract: "entitlement_admin_challenges_v11",
    filename: "20260731_entitlement_admin_challenges_v11.sql",
    checksum: "bd385ad3f5a9bc6ee88b6b0a9429e7368737115b519a11414ce8ed82c39d1ba8",
  },
] satisfies readonly MigrationDefinition[]);
