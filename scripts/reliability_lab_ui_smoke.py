#!/usr/bin/env python3
"""Freeze the Reliability Lab UI architecture before implementation.

This smoke intentionally inspects source instead of rendering the React app. It
is expected to remain RED until the feature-local Reliability Lab routes, API
client, and pages are implemented.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "ui" / "start-building-app" / "src" / "app"
APP = APP_ROOT / "App.tsx"
SIDEBAR = APP_ROOT / "components" / "layout" / "Sidebar.tsx"
FEATURE_ROOT = APP_ROOT / "components" / "pages" / "reliability"
ROUTES = FEATURE_ROOT / "ReliabilityLabRoutes.tsx"
RUN_DETAIL = FEATURE_ROOT / "ReliabilityRunDetail.tsx"
CAMPAIGN_DETAIL = FEATURE_ROOT / "ReliabilityCampaignDetail.tsx"
API = APP_ROOT / "data" / "reliabilityApi.ts"

FEATURE_PAGES = {
    "routes": ROUTES,
    "overview": FEATURE_ROOT / "ReliabilityOverview.tsx",
    "agents": FEATURE_ROOT / "ReliabilityAgents.tsx",
    "scenario_suites": FEATURE_ROOT / "ReliabilityScenarioSuites.tsx",
    "campaigns": FEATURE_ROOT / "ReliabilityCampaigns.tsx",
    "campaign_detail": FEATURE_ROOT / "ReliabilityCampaignDetail.tsx",
    "run_detail": RUN_DETAIL,
    "failures": FEATURE_ROOT / "ReliabilityFailures.tsx",
    "regressions": FEATURE_ROOT / "ReliabilityRegressions.tsx",
    "release_gates": FEATURE_ROOT / "ReliabilityReleaseGates.tsx",
}

FEATURE_ROUTE_PATHS = (
    "agents",
    "scenario-suites",
    "campaigns",
    "campaigns/:id",
    "runs/:id",
    "failures",
    "regressions",
    "release-gates",
)

API_COLLECTION_PATHS = (
    "/reliability/overview",
    "/reliability/agents",
    "/reliability/scenario-suites",
    "/reliability/campaigns",
    "/reliability/failures",
    "/reliability/regressions",
    "/reliability/release-gates",
)

RUN_DETAIL_TEST_IDS = (
    "reliability-run-detail",
    "reliability-run-context-column",
    "reliability-run-trace-column",
    "reliability-run-verdict-column",
)

STATE_TEST_IDS = (
    "reliability-loading-state",
    "reliability-empty-state",
    "reliability-unavailable-state",
)

EVIDENCE_LINK_TEST_IDS = (
    "reliability-run-evidence-chain",
    "reliability-run-campaign-link",
    "reliability-run-mis-evidence-link",
)

FORBIDDEN_PATTERNS = (
    ("authorization_header", re.compile(r"Authorization\s*:", re.IGNORECASE)),
    ("bearer_token_literal", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")),
    ("agent_token_literal", re.compile(r"\bagtok_[A-Za-z0-9_]+\b")),
    ("agent_session_literal", re.compile(r"\bagtsess_[A-Za-z0-9_]+\b")),
    ("openai_key_literal", re.compile(r"\bsk-[A-Za-z0-9._~+/=-]{20,}\b")),
    ("notion_token_literal", re.compile(r"\bntn_[A-Za-z0-9._~+/=-]{8,}\b")),
    ("raw_prompt_field", re.compile(r"(?<![A-Za-z0-9_])raw_prompt\s*[:=]", re.IGNORECASE)),
    ("raw_response_field", re.compile(r"(?<![A-Za-z0-9_])raw_response\s*[:=]", re.IGNORECASE)),
    ("raw_transcript_field", re.compile(r"(?<![A-Za-z0-9_])raw_transcript\s*[:=]", re.IGNORECASE)),
    ("prompt_body_field", re.compile(r"(?<![A-Za-z0-9_])prompt_(?:text|body)\s*[:=]", re.IGNORECASE)),
    ("response_body_field", re.compile(r"(?<![A-Za-z0-9_])response_(?:text|body)\s*[:=]", re.IGNORECASE)),
    ("secret_value_field", re.compile(r"(?<![A-Za-z0-9_])(?:secret|api_key|access_token)\s*[:=]", re.IGNORECASE)),
    ("unsafe_html_render", re.compile(r"dangerouslySetInnerHTML")),
)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read_source(path: Path, label: str, failures: list[str]) -> str:
    if not path.exists():
        failures.append(f"missing {label}: {path.relative_to(ROOT)}")
        return ""
    return path.read_text(encoding="utf-8")


def route_path_count(source: str, path: str) -> int:
    pattern = re.compile(rf"\bpath\s*=\s*[\"']{re.escape(path)}[\"']")
    return len(pattern.findall(source))


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {
        "files": {},
        "routes": {},
        "api_paths": {},
        "state_markers": {},
        "run_detail_markers": {},
        "evidence_links": {},
        "forbidden_hits": [],
    }

    app = read_source(APP, "app router", failures)
    sidebar = read_source(SIDEBAR, "sidebar", failures)
    routes = read_source(ROUTES, "feature-local route table", failures)
    run_detail = read_source(RUN_DETAIL, "Reliability Run Detail", failures)
    campaign_detail = read_source(CAMPAIGN_DETAIL, "Reliability Campaign Detail", failures)
    api = read_source(API, "Reliability API client", failures)

    for label, path in FEATURE_PAGES.items():
        exists = path.exists()
        evidence["files"][label] = {
            "path": str(path.relative_to(ROOT)),
            "exists": exists,
        }
        require(exists, f"missing feature page {label}: {path.relative_to(ROOT)}", failures)

    mount_path = "/workspace/reliability/*"
    mount_count = route_path_count(app, mount_path)
    evidence["routes"]["app_mount"] = {"path": mount_path, "count": mount_count}
    require(mount_count == 1, f"App.tsx must register exactly one lazy {mount_path} mount; found {mount_count}", failures)
    require(
        bool(re.search(r"\blazy\s*\(\s*\(\)\s*=>\s*import\(\s*[\"'][^\"']*reliability/ReliabilityLabRoutes[\"']", app, re.DOTALL)),
        "App.tsx must lazy-import feature-local reliability/ReliabilityLabRoutes",
        failures,
    )
    require("Suspense" in app, "App.tsx must wrap the lazy Reliability Lab mount in Suspense", failures)

    sidebar_paths = re.findall(r"\bpath\s*:\s*[\"'](/workspace/reliability[^\"']*)[\"']", sidebar)
    evidence["routes"]["sidebar_entries"] = sidebar_paths
    require(
        sidebar_paths == ["/workspace/reliability"],
        f"Sidebar must contain one top-level /workspace/reliability entry and no child entries; found {sidebar_paths}",
        failures,
    )
    require('labelKey: "reliabilityLab"' in sidebar or "labelKey: 'reliabilityLab'" in sidebar, "Sidebar entry must use labelKey reliabilityLab", failures)
    require("Reliability Lab" in sidebar, "Sidebar English copy must use the formal label Reliability Lab", failures)

    index_count = len(re.findall(r"<Route\s+index(?:\s|/|>)", routes))
    evidence["routes"]["overview_index_count"] = index_count
    require(index_count == 1, f"Reliability overview must be the single feature index route; found {index_count}", failures)

    for path in FEATURE_ROUTE_PATHS:
        count = route_path_count(routes, path)
        evidence["routes"][path] = count
        require(count == 1, f"feature-local route table must register path={path!r} exactly once; found {count}", failures)
        require(
            route_path_count(app, f"/workspace/reliability/{path}") == 0,
            f"child route {path!r} must stay feature-local instead of being registered in App.tsx",
            failures,
        )

    require("apiJson" in api, "reliabilityApi.ts must reuse the authenticated apiJson client", failures)
    require("/mis-api/reliability" not in api, "reliabilityApi.ts must pass /reliability/... to apiJson, not duplicate the /mis-api base", failures)
    require("/api/reliability" not in api, "reliabilityApi.ts must not bypass the configured /mis-api base", failures)

    for path in API_COLLECTION_PATHS:
        present = path in api
        evidence["api_paths"][path] = present
        require(present, f"reliabilityApi.ts missing collection/readback path {path}", failures)

    detail_api_markers = {
        "campaign_detail": "/reliability/campaigns/${encodeURIComponent(",
        "run_detail": "/reliability/runs/${encodeURIComponent(",
    }
    for label, marker in detail_api_markers.items():
        present = marker in api
        evidence["api_paths"][label] = present
        require(present, f"reliabilityApi.ts missing encoded {label} path marker: {marker}", failures)

    current_gate_contract = {
        "campaign_current_gate_id": "current_gate_id: string | null;" in api,
        "release_gate_is_current": "is_current?: boolean;" in api,
        "run_detail_campaign": "campaign: ReliabilityCampaign;" in api,
        "shared_explicit_selector": "selectReliabilityCurrentGate" in api,
        "campaign_detail_selector": "selectReliabilityCurrentGate(campaign, gates)" in campaign_detail,
        "run_detail_selector": "selectReliabilityCurrentGate(detail?.campaign, detail?.release_gates)" in run_detail,
        "campaign_missing_state": "Current release gate unavailable" in campaign_detail,
        "run_missing_state": "Current release gate unavailable" in run_detail,
    }
    evidence["current_gate_contract"] = current_gate_contract
    for marker, present in current_gate_contract.items():
        require(present, f"Reliability Lab missing explicit current-gate contract marker: {marker}", failures)

    inferred_gate_patterns = {
        "campaign_first_gate": re.compile(r"\bgates\s*\[\s*0\s*\]"),
        "run_positional_gate": re.compile(r"\brelease_gates\s*\[[^\]]+\]"),
        "gate_sort_by_created_at": re.compile(r"(?:release_gates|gates)[\s\S]{0,160}\.sort\([^\n]*created_at"),
        "gate_sort_by_gate_id": re.compile(r"(?:release_gates|gates)[\s\S]{0,160}\.sort\([^\n]*gate_id"),
    }
    inferred_gate_hits = [
        name
        for name, pattern in inferred_gate_patterns.items()
        if pattern.search(f"{campaign_detail}\n{run_detail}")
    ]
    evidence["inferred_current_gate_hits"] = inferred_gate_hits
    require(
        not inferred_gate_hits,
        f"Reliability detail views must not infer a current gate from collection order: {inferred_gate_hits}",
        failures,
    )

    feature_sources: list[str] = []
    if FEATURE_ROOT.exists():
        for path in sorted(FEATURE_ROOT.rglob("*.tsx")):
            feature_sources.append(path.read_text(encoding="utf-8"))
    feature_bundle = "\n".join(feature_sources)
    source_bundle = f"{feature_bundle}\n{api}"

    for marker in STATE_TEST_IDS:
        present = marker in feature_bundle
        evidence["state_markers"][marker] = present
        require(present, f"Reliability Lab missing stable {marker} loading/empty/unavailable marker", failures)

    for marker in RUN_DETAIL_TEST_IDS:
        present = marker in run_detail
        evidence["run_detail_markers"][marker] = present
        require(present, f"Reliability Run Detail missing stable test id {marker}", failures)
    require(
        bool(re.search(r"(?:xl|2xl):grid-cols-\[", run_detail)),
        "Reliability Run Detail must declare a responsive explicit three-column grid",
        failures,
    )

    for marker in EVIDENCE_LINK_TEST_IDS:
        present = marker in run_detail
        evidence["evidence_links"][marker] = present
        require(present, f"Reliability Run Detail missing evidence-chain link marker {marker}", failures)
    for marker in ("campaign_id", "mis_run_id", "/admin/runs/"):
        present = marker in run_detail
        evidence["evidence_links"][marker] = present
        require(present, f"Reliability Run Detail missing explicit evidence-chain reference {marker}", failures)

    forbidden_hits: list[dict[str, str]] = []
    for name, pattern in FORBIDDEN_PATTERNS:
        match = pattern.search(source_bundle)
        if match:
            forbidden_hits.append({"pattern": name, "match": match.group(0)[:80]})
    evidence["forbidden_hits"] = forbidden_hits
    require(not forbidden_hits, f"Reliability Lab source contains forbidden raw/secret markers: {forbidden_hits}", failures)

    result = {
        "operation": "reliability_lab_ui_smoke",
        "ok": not failures,
        "expected_state_before_ui_implementation": "red",
        "failures": failures,
        "evidence": evidence,
        "safety": {
            "source_inspection_only": True,
            "ledger_mutated": False,
            "live_execution_performed": False,
            "raw_prompt_or_response_required": False,
            "token_omitted": True,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
