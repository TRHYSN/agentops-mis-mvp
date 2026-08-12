# Reliability Campaign Execution Contract

This packaged contract is the stable runtime authority referenced by every
OpenCekura campaign plan. A governed deterministic campaign must:

1. validate the Scenario Suite before opening execution;
2. create or reuse the authoritative MIS Task and Plan;
3. record the Campaign as running before simulation begins;
4. persist Conversation Runs, Tool Calls, Evaluations, Artifacts, Evidence,
   and the Release Gate through stable idempotency keys; and
5. fail closed when simulation, evaluation, evidence publication, or MIS
   persistence cannot be completed and verified.

The OpenCekura SQLite projection contains vertical reliability objects and
stable mappings only. The existing AgentOps MIS Task, Plan, Run, ToolCall,
Evaluation, Artifact, Approval, and Audit ledgers remain authoritative.
