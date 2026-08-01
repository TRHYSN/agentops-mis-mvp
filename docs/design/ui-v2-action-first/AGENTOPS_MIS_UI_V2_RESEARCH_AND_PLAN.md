# AgentOps MIS UI V2 — Research, Audit, and Implementation Plan

**Status:** Proposed, Canonical=false
**Evidence date:** 2026-08-01
**Repository audited:** `geogejoy107-jpg/agentops-mis-mvp`
**Verified branch / commit:** `main` @ `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
**Record intent:** GitHub design/plan artifact; Notion receives only the bounded Project Delta after write receipt

## 1. Executive decision

The UI problem is not primarily a palette problem. It is an information-architecture and workflow-priority problem.

The current product exposes business objects, operational tools, admin utilities, visual navigation, and acceptance surfaces as peer-level destinations. The home page then repeats several of those workflows inside one dashboard. This creates three costs:

1. **Orientation cost:** users must first understand the product’s internal object model before they can act.
2. **Decision cost:** the same work can be entered through Home, Dispatch, Tasks, Control Tower, Runs, Approvals, Pixel Office, or reports.
3. **Evidence cost:** the authority chain is technically present but visually fragmented across pages.

The proposed UI V2 is an **action-first control plane**:

> Command Center → Work → Workforce → Review → Evidence → Integrations

Pixel Office remains available as an optional projection, never as the canonical workflow or state authority.

## 2. Project preflight

Repository: `geogejoy107-jpg/agentops-mis-mvp`
Branch: `main`
Commit: `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
Current milestone: **Unknown as a reviewed Canon milestone**. Code fact: `main` includes the Private Host + Codex product bridge and the merged Research Lab runtime-version synchronization through PR #115; the checked-in context snapshot still describes the earlier preview.42 / PR #104 line and is therefore operationally stale.
Current objective: preserve the governed control-plane boundary while making human supervision, approval, delivery review, and evidence retrieval materially simpler.
Relevant approved decisions: D-001 authority split; D-002 candidate memory is not authority; D-003 mandatory Git preflight; D-004 store only Project Delta; D-005 GitHub + Notion authority split; D-006 hardening before horizontal expansion; D-007 preserve durable delta before raw session expiry.
Open P0/P1 items: P0 correctness gates must stay green; P0-11 is recorded as blocked in the stale backlog; P1-05 oversized horizontal module split is in progress. Current truth for these items requires Canon reconciliation because project files predate the verified main head.
Risks / unknowns: no live authenticated product session; no connected GitHub or Notion write tool; no MIS Ledger access; no usability analytics; no verified user-role frequency data; project Canon drift.
Evidence date: 2026-08-01.


## 2.1 Existing UI V2 lineage and reconciliation

The repository already contains a bounded UI V2 implementation line:

- branch: `design/gemini-ui-v2-implementation`;
- Draft PR: `#11`;
- exact historical head: `0fe500d0a21bb02d6d18de3eac864d4b1af7667b`;
- base: the older `codex/agent-gateway-kb-demo` development line;
- delivered scope: semantic tokens, an operational shell, Mission Control panels, responsive screenshots, route preservation, and an optional Pixel Office preview.

That line is useful implementation evidence, but it is not an acceptable current base because it predates the present `main` by substantial Host, Codex bridge, Research Lab, authentication, and route evolution. This proposal therefore **updates rather than supersedes** the historical UI V2 direction. The safe execution path is a fresh branch from the exact current `main`, followed by a route/API/permission inventory before any broad UI change.

The action-first proposal adds a stricter product rule that was not explicit in the old line: daily supervision is organized around decisions and evidence (`Command Center → Work → Workforce → Review → Evidence → Integrations`), while Pixel Office remains an optional non-authoritative projection.

## 3. Audit findings

### F1 — Navigation exposes implementation structure instead of user goals

The current sidebar presents roughly twenty peer destinations across “Client Workspace” and “Admin Console.” Several destinations overlap semantically: Home / Control Tower, AI Employees / Agent Registry, Worker Console / Connectors, Reports / Evaluation / Audit, and Dispatch / Tasks / Runs.

**Impact:** users must learn the MIS schema before they can supervise work.

**Resolution:** six primary destinations, with object-level pages reached contextually:

| New primary destination | Includes |
|---|---|
| Command Center | attention queue, active work, system health, recent deliveries |
| Work | tasks, runs, deliveries, task/run detail |
| Workforce | agents, workers, hosts, capabilities |
| Review | approvals, evaluations, memory candidates |
| Evidence | artifacts, evaluation results, audit, evidence manifests |
| Integrations | runtime connectors, external bases, Private Host connection / acceptance |

Settings contains account/access, templates, appearance, and workspace administration. Pixel Office moves to an optional view switch.

### F2 — Home page is a product tour, dashboard, map, and work queue at the same time

The current home page fetches dashboard, tasks, approvals, runs, and memories together, then presents launch cards and a large Pixel Office preview before the user’s urgent decisions are resolved.

**Impact:** above-the-fold space is consumed by orientation and presentation rather than action.

**Resolution:** the first screen answers only three questions:

1. What requires my decision now?
2. What is blocked or at risk?
3. What is running and healthy?

Charts and aggregate analytics are secondary panels or saved views.

### F3 — Visual language has too many competing product personalities

The UI contains enterprise, ops, and workforce themes, multiple accent colors, page-level inline RGBA styling, gradients, control-tower charts, and Pixel Office visual language.

**Impact:** status colors, brand colors, and decoration compete; users cannot reliably infer meaning from color.

**Resolution:**

- one neutral enterprise foundation;
- one brand accent;
- semantic colors only for success, warning, danger, and information;
- no decorative gradient in canonical work surfaces;
- dark mode may exist, but it is a color-mode transformation, not a different product personality;
- Pixel Office retains expressive styling inside its isolated optional view.

### F4 — The evidence chain is fragmented

Task, Run, Tool Call, Approval, Artifact, Evaluation, Memory, and Audit are exposed as separate ledgers/pages. This is useful for forensic browsing but inefficient for normal review.

**Impact:** “is this actually done?” requires opening multiple destinations and correlating IDs manually.

**Resolution:** every Task and Run detail uses the same evidence-oriented pattern:

- summary header;
- stage strip: Goal → Plan → Execution → Approval → Artifact → Evaluation → Handoff;
- Overview / Execution / Evidence / Audit tabs;
- exact-head, plan hash, acceptance state, and verifier result visible without opening raw records;
- ledgers remain available as filtered forensic views under Evidence.

### F5 — Primary and risky actions do not have a consistent hierarchy

Approvals currently surface direct action buttons in card contexts while other actions are spread through page headers, links, and cards.

**Impact:** the visual weight of an action does not consistently communicate consequence.

**Resolution:**

- one primary action per view;
- risky write actions always show an explicit Prepared Action detail;
- normalized args, action hash, expiry, provider side-effect state, and exact resume status are visible before approval;
- reject and approve stay physically separated and keyboard reachable;
- contextual actions move to row selection, detail headers, or the command palette.

### F6 — Accessibility and responsive behavior need to be acceptance criteria, not cleanup

The UI is dense, uses small text and compact controls, and relies heavily on desktop side navigation.

**Resolution:**

- minimum 24×24 CSS-pixel pointer targets, with common buttons 36–40 px high;
- visible focus ring on every interactive control;
- no action available only by drag;
- mobile review actions wrap into a stable bottom/header action area;
- tables reduce columns but preserve status and next action;
- keyboard command/search available globally.

## 4. Reference patterns and what to borrow

| Reference | Borrowed idea | MIS authority boundary preserved |
|---|---|---|
| Linear | global command/search, contextual row actions, fast list navigation | Linear never owns MIS Task/Run/Approval state |
| Sentry | strong hierarchy and progressive disclosure on complex detail pages | Sentry does not define MIS evidence semantics |
| GitHub Actions | run summary → job/step detail; failed steps expanded first | GitHub is code/CI authority only; MIS remains run/evidence authority |
| Temporal Web UI | filterable execution list and detail tabs for history/relationships/metadata | Temporal remains a runtime reference, not MIS source of truth |
| Carbon + WCAG | disclosure patterns, empty-state guidance, target size and focus visibility | component guidance only; MIS permissions and state transitions remain first-party |

## 5. Proposed information architecture

```text
AgentOps MIS
├── Command Center
│   ├── Needs attention
│   ├── Active work
│   ├── System health
│   └── Recent deliveries
├── Work
│   ├── Tasks
│   ├── Runs
│   ├── Deliveries
│   └── Task / Run detail
├── Workforce
│   ├── Agents
│   ├── Workers
│   ├── Hosts
│   └── Capability / trust manifests
├── Review
│   ├── Approvals
│   ├── Evaluations
│   └── Memory candidates
├── Evidence
│   ├── Delivery evidence packages
│   ├── Artifacts
│   ├── Evaluations
│   ├── Audit
│   └── Forensic ledgers
├── Integrations
│   ├── Codex / Hermes / OpenClaw
│   ├── Notion and external bases
│   └── Private Host connection / acceptance
├── Office view (optional projection)
└── Settings
    ├── Workspace
    ├── Members and access
    ├── Templates
    ├── Appearance
    └── Private Host administration
```

## 6. Legacy route migration concept

Keep redirects for bookmarks and tests during migration.

| Current route | New location |
|---|---|
| `/workspace` | `/command` |
| `/workspace/dispatch` | `/work/new` |
| `/workspace/tasks` | `/work?tab=tasks` |
| `/admin/runs` | `/work?tab=runs` |
| `/admin/runs/:id` | `/work/runs/:id` |
| `/workspace/agents` | `/workforce?tab=agents` |
| `/workspace/workers` | `/workforce?tab=workers` |
| `/workspace/approvals` | `/review?tab=approvals` |
| `/admin/evaluations` | `/review?tab=evaluations` |
| `/workspace/memory` | `/review?tab=memory` |
| `/admin/toolcalls` | `/evidence?view=toolcalls` |
| `/admin/audit` | `/evidence?view=audit` |
| `/workspace/reports` | `/evidence?view=deliveries` |
| `/admin/connectors` | `/integrations` |
| `/admin/connectors/codex` | `/integrations/codex` |
| `/admin/bases/notion` | `/integrations/notion` |
| `/admin/private-host-acceptance` | `/integrations/private-host/acceptance` |
| `/workspace/pixel-office` | `/office` |
| `/workspace/account` | `/settings/access` |
| `/admin/templates` | `/settings/templates` |

## 7. Agent Plan

### Goal

Create a coherent, lower-friction supervision interface without changing MIS authority semantics or backend object contracts.

### Scope

- navigation and route compatibility layer;
- UI tokens and component primitives;
- Command Center;
- Work list and Task/Run details;
- Review queue;
- Evidence package views;
- Workforce and Integration grouping;
- optional Office view entry;
- responsive and keyboard behavior;
- visual and interaction regression tests.

### Out of scope

- database schema;
- authority model;
- task/run/approval state-machine changes;
- runtime adapter behavior;
- auto-promotion of Memory;
- removal of Pixel Office;
- migration from Vite to another framework in the same work package.

### Work packages

#### WP0 — Evidence baseline and route inventory

- Capture current route map, screenshots, viewport matrix, and core task paths.
- Record current API calls per page.
- Define legacy route redirect tests.

Acceptance:
- every existing route has owner, disposition, and migration target;
- screenshots at desktop, tablet, and mobile widths;
- no implementation begins if exact head changes without plan rebind.

#### WP1 — Design foundation and shell

- Introduce a single token set and reusable Button, Badge, Panel, Table, Tabs, EmptyState, PageHeader, and FocusRing behavior.
- Implement new sidebar, top bar, workspace switcher, and command palette.
- Preserve old routes through redirects.

Acceptance:
- primary navigation has at most six operating destinations;
- all interactive controls are keyboard reachable;
- minimum target size checks pass;
- old URL contract tests pass.

#### WP2 — Command Center

- Build the action queue from the existing operator Command Center BFF where possible.
- Show attention, active work, system health, and recent deliveries.
- Remove Pixel Office preview and general analytics from above the fold.

Acceptance:
- a user can reach a blocked run, pending approval, or stale worker in one click;
- create/dispatch task in one click;
- no duplicate fetches when the BFF already provides the data;
- empty and error states identify the cause and next action.

#### WP3 — Work and Run Detail

- Merge Tasks, Runs, and Deliveries into one Work destination.
- Implement summary → stage strip → tabs pattern.
- Show exact head, bound plan, stop condition, artifacts, evaluation, verifier, and handoff.

Acceptance:
- “is this really complete?” can be answered on one detail page;
- failed checks are expanded or prioritized;
- forensic raw records remain linked, not removed;
- no state transition occurs from read-only summary components.

#### WP4 — Review queue

- Combine approvals, evaluations, and Memory candidates into filtered tabs.
- Implement master-detail review layout.
- Make Prepared Action details explicit.

Acceptance:
- action hash, normalized args, risk, policy, expiry, and side-effect status appear before approval;
- exact resume mismatch remains blocking;
- approve/reject are keyboard accessible and have confirm behavior appropriate to risk;
- Memory candidates cannot be visually mistaken for approved Memory.

#### WP5 — Evidence, Workforce, and Integrations

- Group forensic objects into evidence packages and contextual detail tabs.
- Group Agent/Worker/Host/Capabilities.
- Group connectors/external bases/Private Host acceptance.

Acceptance:
- no authority object is deleted or redefined;
- users can export/read back evidence manifests;
- connection cards display capability and trust state, not just “connected.”

#### WP6 — Verification and rollout

- Desktop/tablet/mobile visual regression.
- Keyboard-only scenario tests.
- Legacy route contract tests.
- Five task-based usability scenarios.
- Feature flag or staged route switch; then delete dead UI only after acceptance.

Acceptance targets:
- ≥90% task completion across five scripted scenarios;
- median time-to-pending-approval ≤20 seconds from Command Center;
- median time-to-verify-delivery ≤45 seconds;
- zero inaccessible primary actions;
- zero broken legacy routes;
- exact-head UI build and deterministic smoke checks green;
- independent verifier approval.

### Risks

1. The current route split may encode permission assumptions not visible from public code inspection.
2. A broad visual rewrite could accidentally alter high-risk approval semantics.
3. The home page currently aggregates multiple APIs; replacing it may uncover missing BFF fields.
4. Reducing navigation can hide forensic tools from expert users unless command/search and deep links remain.
5. Canon drift can cause implementation against a stale milestone.
6. Pixel Office stakeholders may perceive de-emphasis as removal; keep it as a first-class optional projection.

### Stop conditions

- GitHub HEAD differs from the plan-bound SHA and cannot be reconciled.
- route, auth, or API contract is unknown for a modified surface;
- any UI component begins owning Task/Run/Approval/Memory state outside MIS APIs;
- exact resume or Prepared Action evidence is weakened;
- legacy route tests cannot be retained during migration;
- verifier is the same actor as implementer for final acceptance.

## 8. Prototype coverage

The accompanying standalone HTML prototype includes:

- new shell and condensed navigation;
- Command Center;
- Work list;
- Run detail with evidence chain;
- Review master-detail experience;
- Workforce;
- Evidence;
- Integrations;
- optional Office view;
- Settings consolidation;
- responsive layouts;
- global command palette (`Cmd/Ctrl + K`);
- visible focus behavior and target sizes.

It is a design/interaction artifact, not a claim that the repository has been modified.

## 9. Project Delta candidate

```yaml
type: Requirement
title: Replace fragmented MIS UI with an action-first control plane
status: Proposed
canonical: false
priority: P1
module: Web UI / Information Architecture
summary: >
  Consolidate daily operation into Command Center, Work, Workforce, Review,
  Evidence, and Integrations; keep Pixel Office as an optional non-authoritative
  projection; preserve all MIS authority and evidence semantics.
source: User request on 2026-08-01 plus repository UI audit
repository: geogejoy107-jpg/agentops-mis-mvp
branch: main
commit: 99ce51d693f1d646ea84acc2f7f376bde1a95a9a
duplicate_of: null
updates: Existing UI/productization direction and P1-05 module-splitting work
supersedes: null
conflicts_with: null
owner: Project owner
next_action: Review the docs-only recovery branch `docs/ui-v2-action-first-20260801`, then create a separately approved implementation task; do not force-push the stale PR #11 line
```

## 10. Record status

- Target GitHub branch: `docs/ui-v2-action-first-20260801`
- Target GitHub base: `main` @ `99ce51d693f1d646ea84acc2f7f376bde1a95a9a`
- Existing UI V2 lineage: Draft PR #11 / `design/gemini-ui-v2-implementation` @ `0fe500d0a21bb02d6d18de3eac864d4b1af7667b`; preserved as historical evidence, not reused as a current base
- Notion classification: `Requirement`, `Proposed`, `Canonical=false`
- Canonical project state: unchanged until human review and implementation evidence

## 11. Evidence index

### Repository evidence inspected

- `docs/project/PROJECT_STATE.md`
- `docs/project/DECISIONS.md`
- `docs/project/BACKLOG.md`
- `docs/project/HANDOFF.md`
- `docs/project/PROJECT_OPERATING_RULES.md`
- `AGENTS.md`
- `PROJECT_SPEC.md`
- `AGENT_WORKFLOW.md`
- `BASE_INDEX.md`
- `docs/project/CURRENT_CONTEXT_SNAPSHOT.md`
- `docs/OPEN_SOURCE_ADOPTION_BOUNDARY_SPEC.md`
- `ui/start-building-app/src/app/components/layout/Sidebar.tsx`
- `ui/start-building-app/src/app/components/pages/WorkspaceHome.tsx`
- `ui/start-building-app/src/styles/theme.css`
- route declarations and relevant detail/list surfaces under `ui/start-building-app/src/app/`

### Official external references

- Linear Docs — issue selection, keyboard shortcuts, contextual actions, command menu.
- GitHub Docs — workflow run summary, job/step drill-down, failure-first log inspection.
- Temporal Platform Documentation — filterable execution lists, saved views, and execution detail metadata.
- W3C WAI / WCAG 2.2 — target size minimum and visible focus appearance.
- Nielsen Norman Group — progressive disclosure.
- Carbon Design System — actionable and contextual empty states.

### Evidence limitations

- Public GitHub inspection does not substitute for an authenticated local checkout, exact-head build, route contract test, or CI run.
- The Notion collaboration record must receive only the bounded Project Delta and must preserve `Proposed / Canonical=false` until review.
- MIS Ledger/API was not available, so no Task, Plan, Run, Approval, Artifact, Evaluation, Memory Review, or Audit record was written.
- The prototype uses representative sample data only; it must not be interpreted as live MIS state.
