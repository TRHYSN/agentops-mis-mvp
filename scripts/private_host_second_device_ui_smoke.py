#!/usr/bin/env python3
"""Statically verify the bounded second-device Private Host browser checklist."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ui" / "start-building-app" / "src" / "app" / "App.tsx"
SIDEBAR = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "layout" / "Sidebar.tsx"
PAGE = ROOT / "ui" / "start-building-app" / "src" / "app" / "components" / "pages" / "PrivateHostAcceptance.tsx"
LIVE_API = ROOT / "ui" / "start-building-app" / "src" / "app" / "data" / "liveApi.ts"

SECRET_PATTERNS = [
    re.compile(r"Authorization:", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{20,}"),
]

EXPECTED = {
    "route_import": (APP, 'import { PrivateHostAcceptance } from "./components/pages/PrivateHostAcceptance";'),
    "route_path": (APP, 'path="/admin/private-host-acceptance" element={<PrivateHostAcceptance />}'),
    "sidebar_path": (SIDEBAR, 'path: "/admin/private-host-acceptance"'),
    "sidebar_en": (SIDEBAR, 'privateHostAcceptance: "Private Host Acceptance"'),
    "sidebar_zh": (SIDEBAR, 'privateHostAcceptance: "私有主机验收"'),
    "page_boundary_en": (PAGE, "Non-authoritative browser checklist"),
    "page_boundary_zh": (PAGE, "非权威浏览器检查清单"),
    "receipt_type": (PAGE, 'receipt_type: "device_checklist_receipt"'),
    "receipt_non_authoritative": (PAGE, "non_authoritative: true"),
    "origin_only": (PAGE, "location_origin: window.location.origin"),
    "manual_second_device": (PAGE, 'second_device: "Opened from a separate physical device"'),
    "manual_disconnect": (PAGE, "disconnect_reconnect"),
    "snapshot_loader": (LIVE_API, "loadPrivateHostAcceptanceSnapshot"),
    "marker_creator": (LIVE_API, "createPrivateHostAcceptanceMarker"),
    "human_session": (LIVE_API, 'id: "human_session"'),
    "readiness": (LIVE_API, 'apiJson<Record<string, unknown>>("/local/readiness")'),
    "task_read": (LIVE_API, 'listCheck("tasks_readable", "/tasks", "tasks")'),
    "evaluation_read": (LIVE_API, 'listCheck("evaluations_readable", "/evaluations", "evaluations")'),
    "approval_read": (LIVE_API, 'listCheck("approvals_readable", "/approvals", "approvals")'),
    "memory_read": (LIVE_API, 'listCheck("memories_readable", "/memories", "memories")'),
    "audit_read": (LIVE_API, 'listCheck("audit_readable", "/audit?limit=150", "audit_logs")'),
    "artifact_read": (LIVE_API, 'listCheck("artifacts_readable", "/artifacts", "artifacts")'),
    "marker_low_risk": (LIVE_API, 'risk_level: "low"'),
    "marker_zero_budget": (LIVE_API, "budget_limit_usd: 0"),
    "authority_type": (LIVE_API, "export interface PrivateHostAuthorityReceipt"),
    "authority_create": (LIVE_API, "createPrivateHostAuthorityReceipt"),
    "authority_readback": (LIVE_API, "loadPrivateHostAuthorityReceipt(created.receipt_id)"),
    "authority_post": (LIVE_API, 'apiJson<unknown>("/host/acceptance-receipts"'),
    "authority_hash_validation": (LIVE_API, "/^[a-f0-9]{64}$/"),
    "authority_section": (PAGE, 'data-testid="private-host-authority-receipt"'),
    "authority_owner_gate": (PAGE, 'user?.role !== "owner"'),
    "authority_download": (PAGE, '/mis-api/host/acceptance-receipts/${encodeURIComponent(authorityReceipt.receipt_id)}/download'),
    "authority_en": (PAGE, 'authorityTitle: "Owner authority receipt"'),
    "authority_zh": (PAGE, 'authorityTitle: "Owner 权威回执"'),
}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    texts: dict[Path, str] = {}
    for name, (path, marker) in EXPECTED.items():
        require(path.exists(), f"{name}: missing file {path.relative_to(ROOT)}", failures)
        text = texts.setdefault(path, path.read_text(encoding="utf-8") if path.exists() else "")
        require(marker in text, f"{name}: missing marker {marker}", failures)

    combined = "\n".join(texts.values())
    page = texts.get(PAGE, "")
    live_api = texts.get(LIVE_API, "")
    require("window.location.href" not in page, "receipt must not capture URL path or query", failures)
    require("document.cookie" not in combined, "checklist must not read browser cookies", failures)
    require("localStorage" not in combined, "checklist must not persist receipt data in localStorage", failures)
    require("runCustomer" not in page and "dispatchLocalWorker" not in page, "checklist page must not invoke a Runtime workflow", failures)
    require("No Runtime or external connector is invoked" in live_api, "marker must declare the no-Runtime boundary", failures)
    require("Host Artifact" in page and "authoritative Audit" in page and "Owner authority receipt" in page, "page must distinguish the Host authority receipt", failures)
    require("generated_by_user_id" not in page, "authority UI must not display the receipt generator account id", failures)
    require("authorityReceipt.workspace_id" not in page, "authority UI must not display the receipt workspace id", failures)
    require(not any(pattern.search(combined) for pattern in SECRET_PATTERNS), "UI source contains token-like material", failures)

    output = {
        "operation": "private_host_second_device_ui_smoke",
        "ok": not failures,
        "route": "/admin/private-host-acceptance",
        "checks": len(EXPECTED) + 9,
        "failures": failures,
        "safety": {
            "static_only": True,
            "receipt_non_authoritative": True,
            "runtime_called": False,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
