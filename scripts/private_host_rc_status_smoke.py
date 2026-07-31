#!/usr/bin/env python3
"""Verify the current Private Host prerelease and open physical gates stay explicit."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RC = ROOT / "docs" / "PRIVATE_HOST_RC_ACCEPTANCE.md"
SECOND_DEVICE = ROOT / "docs" / "PRIVATE_HOST_SECOND_DEVICE_ACCEPTANCE.md"
SERVICE_UPGRADE = ROOT / "docs" / "PRIVATE_HOST_SERVICE_UPGRADE_MIGRATION_ACCEPTANCE.md"
REAL_RUNTIME = ROOT / "docs" / "PRIVATE_HOST_REAL_RUNTIME_CLIENT_ACCEPTANCE.md"
WORKER_INTAKE_HEARTBEAT = ROOT / "docs" / "PRIVATE_HOST_WORKER_INTAKE_HEARTBEAT_ACCEPTANCE.md"
HUMAN_SERVICE_RECEIPT = ROOT / "docs" / "PRIVATE_HOST_HUMAN_SERVICE_RECEIPT_ACCEPTANCE.md"
WORKER_HEARTBEAT_CADENCE = ROOT / "scripts" / "worker_service_heartbeat_cadence_smoke.py"

VERSION = "1.6.0-private-host-preview.39"
TAG = f"v{VERSION}"
COMMIT = "17801e3bbb20cdaec68e72f3e225ab5492d8f8e2"
RELEASE_URL = f"https://github.com/geogejoy107-jpg/agentops-mis-mvp/releases/tag/{TAG}"
PREVIOUS_VERSION = "1.6.0-private-host-preview.38"
PREVIOUS_TAG = f"v{PREVIOUS_VERSION}"
PREVIOUS_COMMIT = "ee3d36c9ae4f123261893376fff012e36fc8a973"
PHYSICAL_VERSION = "1.6.0-private-host-preview.36"
PHYSICAL_TAG = f"v{PHYSICAL_VERSION}"
PHYSICAL_COMMIT = "a5c7d559cfce5157b10401e34204a6b6a405a554"
LEGACY_PHYSICAL_VERSION = "1.6.0-private-host-preview.35"
LEGACY_PHYSICAL_TAG = f"v{LEGACY_PHYSICAL_VERSION}"
LEGACY_PHYSICAL_COMMIT = "6424ec144013517b21438cd7e528c6db106a0a5e"
CHECKSUMS = {
    "provenance": "10ce325f46b232be18923b838176921bfb2acff32ef04d5f56fa5d489ffa708f",
    "manifest": "4df97caf263b2db7c90507f91268373ba59050b7621e48cf0735bf6fc1a4bead",
    "tar": "dec94e888004cb694a571b11e155311501a2458726aabaa48d34ab9371b4d9ef",
    "zip": "ea4692c7918f85e0e51087cc23e201141cf465e6a2f501fc2eceb3a0e7bc04c6",
    "bootstrap": "75854f364502722eb24d5a7df3c0fc26685bf25acae6d5926e4c6396d16bd812",
}


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def heading_section(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start < 0:
        return ""
    remainder = text[start:]
    next_heading = remainder.find("\n## ", len(marker))
    return remainder if next_heading < 0 else remainder[:next_heading]


def main() -> int:
    failures: list[str] = []
    rc = RC.read_text(encoding="utf-8")
    second = SECOND_DEVICE.read_text(encoding="utf-8")
    service_upgrade = SERVICE_UPGRADE.read_text(encoding="utf-8")
    runtime = REAL_RUNTIME.read_text(encoding="utf-8")
    worker_intake_heartbeat = WORKER_INTAKE_HEARTBEAT.read_text(encoding="utf-8")
    human_service_receipt = HUMAN_SERVICE_RECEIPT.read_text(encoding="utf-8")
    rc_headings = [line for line in rc.splitlines() if line.startswith("## Current Preview ")]
    current_rc = heading_section(rc, "Current Preview 39")
    previous_rc = heading_section(rc, "Superseded Preview 38")
    second_preview38_physical = heading_section(second, "Preview 38 Authenticated MacBook Review")
    second_preview36_host = heading_section(second, "Preview 36 Host Staging")
    second_preview36_physical = heading_section(second, "Preview 36 Physical MacBook Retest")
    second_preview35_physical = heading_section(second, "Preview 35 Authenticated MacBook Evidence")
    service_preview39 = heading_section(service_upgrade, "Preview 39 Release And Real Upgrade")
    service_preview38 = heading_section(service_upgrade, "Preview 38 Release And Real Upgrade")
    service_preview37 = heading_section(service_upgrade, "Preview 37 Release And Real Upgrade")
    service_preview36 = heading_section(service_upgrade, "Preview 36 Release And Real Upgrade")
    runtime_preview39 = heading_section(runtime, "Exact-Package Preview 39 Persistent Worker Result")
    runtime_preview38 = heading_section(runtime, "Exact-Package Preview 38 Host-Local Result")
    runtime_preview36_host = heading_section(runtime, "Exact-Package Preview 36 Negated Read-Only Result")
    runtime_preview36_physical = heading_section(runtime, "Exact-Package Preview 36 Physical MacBook Result")
    normalized_current_rc = " ".join(current_rc.split())
    normalized_previous_rc = " ".join(previous_rc.split())
    normalized_second = " ".join(second.split())
    normalized_second_preview38_physical = " ".join(second_preview38_physical.split())
    normalized_second_preview36_physical = " ".join(second_preview36_physical.split())
    normalized_second_preview35_physical = " ".join(second_preview35_physical.split())
    normalized_service_preview39 = " ".join(service_preview39.split())
    normalized_service_preview38 = " ".join(service_preview38.split())
    normalized_service_preview37 = " ".join(service_preview37.split())
    normalized_service_preview36 = " ".join(service_preview36.split())
    normalized_runtime = " ".join(runtime.split())
    normalized_runtime_preview39 = " ".join(runtime_preview39.split())
    normalized_runtime_preview38 = " ".join(runtime_preview38.split())
    normalized_runtime_preview36_host = " ".join(runtime_preview36_host.split())
    normalized_worker_intake_heartbeat = " ".join(worker_intake_heartbeat.split())
    normalized_human_service_receipt = " ".join(human_service_receipt.split())

    require(len(rc_headings) == 1, "RC document must name exactly one Current Preview", failures)
    require("## Current Preview 39" in rc, "preview.39 must be the current RC prerelease", failures)
    require("## Superseded Preview 38" in rc, "preview.38 Worker finding and physical history must be preserved", failures)
    require("## Superseded Preview 37" in rc, "preview.37 Worker finding must be preserved as superseded history", failures)
    require("## Superseded Preview 36" in rc, "preview.36 physical evidence must be preserved as superseded history", failures)
    require("## Superseded Preview 35" in rc, "preview.35 physical evidence must be preserved as superseded history", failures)
    require("## Superseded Preview 31" in rc, "preview.31 must be preserved as superseded history", failures)
    require("## Superseded Preview 30" in rc, "preview.30 must preserve its immutable upgrade-defect history", failures)
    require("## Superseded Preview 29" in rc, "preview.29 must be preserved as superseded history", failures)
    require("## Superseded Preview 28" in rc, "preview.28 must be preserved as superseded history", failures)
    require("## Superseded Preview 27" in rc, "preview.27 must be preserved as superseded history", failures)
    require("## Superseded Preview 26" in rc, "preview.26 must be preserved as superseded history", failures)
    require("## Superseded Preview 25" in rc, "preview.25 must be preserved as superseded history", failures)
    require("## Superseded Preview 24" in rc, "preview.24 must be preserved as superseded history", failures)
    require("## Superseded Preview 23" in rc, "preview.23 must be preserved as superseded history", failures)
    require("## Superseded Preview 22" in rc, "preview.22 must be preserved as superseded history", failures)
    require("## Superseded Preview 21" in rc, "preview.21 must be preserved as superseded history", failures)
    require("## Superseded Preview 20" in rc, "preview.20 must be preserved as superseded history", failures)
    require("## Superseded Preview 19" in rc, "preview.19 must be preserved as superseded history", failures)
    require("## Superseded Preview 12" in rc, "preview.12 must be marked superseded", failures)
    require(TAG in current_rc and VERSION in current_rc, "current version/tag missing from current RC section", failures)
    require(COMMIT in current_rc and COMMIT in service_preview39,
            "exact release commit missing from current acceptance evidence", failures)
    require(RELEASE_URL in current_rc, "public prerelease URL missing from current RC section", failures)
    for label, checksum in CHECKSUMS.items():
        require(checksum in current_rc, f"{label} checksum missing from current RC section", failures)

    open_gate_markers = (
        "deployed Relay/DNS/TLS",
        "no-Tailscale browser pairing",
        "deployed-Relay interruption",
        "current-package physical browser disconnect/reconnect",
        "current-package physical browser review",
        "Host logout/reboot recovery",
        "another-Mac clean installation",
        "bounded Host log rotation",
    )
    for marker in open_gate_markers:
        require(marker.lower() in normalized_current_rc.lower(), f"open external gate is no longer explicit in current RC section: {marker}", failures)
    require("The current preview therefore remains a prerelease." in normalized_current_rc,
            "current preview must not claim final RC", failures)
    require(TAG in service_preview39 and COMMIT in service_preview39,
            "preview.39 service-upgrade receipt is not exact-package bound", failures)
    require("public-network consumer completed no-repository" in normalized_service_preview39,
            "preview.39 separate release-consumer receipt missing", failures)
    require("preserved the authority ledger" in normalized_service_preview39
            and "Four bounded samples spanning 105 seconds" in normalized_service_preview39,
            "preview.39 data or sustained Worker receipt missing", failures)
    require("Funnel remained disabled" in normalized_service_preview39,
            "preview.39 upgrade must preserve the private transport boundary", failures)
    require("run_gw_0dfd8981a340" in service_preview39
            and "run_gw_a46813718b06" in service_preview39
            and "zero Approval and zero Prepared Action" in normalized_service_preview39,
            "preview.39 persistent Runtime receipt is incomplete", failures)
    require(PREVIOUS_TAG in previous_rc and PREVIOUS_COMMIT in previous_rc,
            "preview.38 immutable history is not exact-package bound", failures)
    require("not sustained acceptance" in normalized_service_preview38
            and "later exact package" in normalized_service_preview38,
            "preview.38 stale-Fleet finding is no longer explicit", failures)
    require("requires a later package" in normalized_service_preview37
            and "not attributed to preview.37" in normalized_service_preview37,
            "preview.37 source-only Worker correction boundary missing", failures)
    require("60 seconds" in normalized_worker_intake_heartbeat
            and "15 minutes" in normalized_worker_intake_heartbeat,
            "Worker heartbeat request and ledger cadence contract missing", failures)
    require("failure is returned to the Worker loop" in normalized_worker_intake_heartbeat,
            "Worker heartbeat rejection observability contract missing", failures)
    require(
        "Fleet liveness is bound to the selected full-scope execution Session."
        in worker_intake_heartbeat,
        "Session-bound Fleet liveness contract missing",
        failures,
    )
    require(
        "heartbeat-only Session cannot keep an execution Session fresh"
        in normalized_worker_intake_heartbeat
        and "No enrollment or unscoped Runtime Event fallback is used"
        in normalized_worker_intake_heartbeat,
        "Fleet Session isolation or fail-closed fallback contract missing",
        failures,
    )
    require(
        "Human and Host Fleet reads are workspace-scoped"
        in normalized_worker_intake_heartbeat
        and "Mixed-offset Session timestamps are normalized to UTC"
        in normalized_worker_intake_heartbeat,
        "Fleet workspace isolation or UTC ordering contract missing",
        failures,
    )
    require(
        "Global `agents.status` is descriptive registration state"
        in normalized_worker_intake_heartbeat
        and "newly minted but unobserved Session cannot shadow a healthy Worker"
        in normalized_worker_intake_heartbeat,
        "Fleet capacity authority or concurrent-Session selection contract missing",
        failures,
    )
    require(
        "Human Fleet hygiene preview/apply" in normalized_worker_intake_heartbeat
        and "Commander Project Board/Inbox" in normalized_worker_intake_heartbeat
        and "Review Queue" in normalized_worker_intake_heartbeat
        and "Customer Delivery Board" in normalized_worker_intake_heartbeat
        and "Operator Action Plan/Command Center/Health" in normalized_worker_intake_heartbeat
        and "task, run," in normalized_worker_intake_heartbeat
        and "approval, memory, artifact" in normalized_worker_intake_heartbeat
        and "neither returned nor mutated" in normalized_worker_intake_heartbeat,
        "Human workspace-scoped Worker, review and delivery boundary missing",
        failures,
    )
    require(
        "core Agent/task/run/tool-call/" in normalized_worker_intake_heartbeat
        and "Cross-workspace object IDs fail closed as `404`"
        in normalized_worker_intake_heartbeat
        and "not a claim of complete hosted multi-tenancy"
        in normalized_worker_intake_heartbeat,
        "Human core read/write workspace authority or its hosted limitation is missing",
        failures,
    )
    require(
        "Run graph parent/child/delegation traversal is scoped at the query itself"
        in normalized_worker_intake_heartbeat
        and "task and run links disagree on workspace fail closed"
        in normalized_worker_intake_heartbeat,
        "related-run graph or conflicting authority-link fail-closed contract missing",
        failures,
    )
    require(
        "Human Commander/Operator/workflow/Worker mutations receive a server-bound workspace body and header"
        in normalized_worker_intake_heartbeat
        and "reject foreign task, artifact or run IDs before any ledger or local workspace side effect"
        in normalized_worker_intake_heartbeat,
        "Human Commander/Operator mutation authority contract missing",
        failures,
    )
    require(
        "newest fresh execution-ready" in normalized_worker_intake_heartbeat
        and "mixed healthy/non-ready replicas retain one deduplicated Worker capacity"
        in normalized_worker_intake_heartbeat
        and "Fleet `attention`" in normalized_worker_intake_heartbeat,
        "mixed execution Session selection or degraded Fleet evidence contract missing",
        failures,
    )
    require(
        "exact selected full-scope execution Session" in normalized_current_rc
        and "missing or mismatched observations fail closed" in normalized_current_rc,
        "current RC does not record the hardened Session-bound source boundary",
        failures,
    )
    require(
        "starts with the preview.38 per-Agent heartbeat table"
        in normalized_worker_intake_heartbeat
        and "does not promote historical evidence into current capacity"
        in normalized_worker_intake_heartbeat,
        "preview.38 heartbeat schema migration boundary missing",
        failures,
    )
    require(
        "15-minute per-Agent ledger sampling row is not Fleet freshness authority"
        in normalized_worker_intake_heartbeat,
        "per-Agent ledger sampling regained Fleet freshness authority",
        failures,
    )
    require(
        "Fresh `paused`, `error`, or `disabled` heartbeats remain observable"
        in normalized_worker_intake_heartbeat
        and "contribute zero execution capacity" in normalized_worker_intake_heartbeat,
        "non-capacity heartbeat states are not locked into RC acceptance",
        failures,
    )
    require(
        "After a preview.38 schema upgrade, Fleet remains `never_seen`"
        in normalized_worker_intake_heartbeat
        and "authenticated execution Session sends its first heartbeat"
        in normalized_worker_intake_heartbeat,
        "legacy upgrade first-heartbeat requirement missing",
        failures,
    )
    require(PREVIOUS_TAG in worker_intake_heartbeat and PREVIOUS_COMMIT in worker_intake_heartbeat,
            "preview.38 Worker heartbeat finding is not exact-package bound", failures)
    require("two execution-capacity service Workers" in normalized_worker_intake_heartbeat
            and "not sustained Host-machine Session Fleet liveness" in normalized_worker_intake_heartbeat
            and "later exact package" in normalized_worker_intake_heartbeat,
            "Worker heartbeat real finding or package boundary is missing", failures)
    require(WORKER_HEARTBEAT_CADENCE.is_file(),
            "Worker heartbeat cadence regression is missing", failures)
    require("250 Fleet" in worker_intake_heartbeat
            and "zero periodic" in normalized_worker_intake_heartbeat
            and "115 MiB" in worker_intake_heartbeat,
            "post-acceptance storage-pressure finding or cadence evidence is missing", failures)
    require("backup retention/prune command" in normalized_current_rc
            and "Host free-space preflight" in normalized_current_rc
            and "approximately 2.63 GB" in normalized_current_rc
            and "Codex, OpenClaw and Docker state was not deleted" in normalized_current_rc,
            "preview.39 storage recovery or retention boundary is missing", failures)
    require(
        "passed on the installed preview.39 Host" in normalized_human_service_receipt
        and "oar_9f5d96f32445" in human_service_receipt
        and "oar_225da0adaa7f" in human_service_receipt
        and "No service-control or Runtime execution was performed"
        in normalized_human_service_receipt,
        "preview.39 authenticated Human Session service receipt is incomplete",
        failures,
    )

    require(
        "Status: advanced Tailscale physical browser workflow partially accepted" in second
        and "ordinary browser-only Relay protocol pending" in second,
        "second-device document must identify Tailscale as an advanced fallback and keep browser-only Relay pending",
        failures,
    )
    require(
        "cannot close the browser-only Console gate" in normalized_second,
        "advanced Tailscale receipt must not substitute for browser-only Console acceptance",
        failures,
    )
    require("Automated browser runtimes outside the Host tailnet are not accepted as a substitute." in normalized_second,
            "physical evidence anti-substitution boundary missing", failures)
    require("## Preview 38 Authenticated MacBook Review" in second,
            "preview.38 physical MacBook review receipt missing", failures)
    require(PREVIOUS_TAG in second_preview38_physical and PREVIOUS_COMMIT in second_preview38_physical,
            "preview.38 physical MacBook receipt is not exact-package bound", failures)
    require("run_gw_c835b4dab9a9" in second_preview38_physical
            and "run_gw_be0e8275670f" in second_preview38_physical,
            "preview.38 physical Run review is incomplete", failures)
    require("3dbe03f31d9c42ffb15f53f18b9b85e010d0d85d7370b89174a788f903e9f6b9"
            in second_preview38_physical
            and "protected Dashboard request returned HTTP 401" in normalized_second_preview38_physical,
            "preview.38 physical approval/download/logout proof missing", failures)
    require("## Preview 36 Host Staging" in second,
            "preview.36 Host staging receipt missing", failures)
    require(PHYSICAL_TAG in second_preview36_host and PHYSICAL_COMMIT in second_preview36_host,
            "preview.36 Host staging is not exact-package bound", failures)
    require("## Preview 36 Physical MacBook Retest" in second,
            "preview.36 physical MacBook retest receipt missing", failures)
    require(PHYSICAL_TAG in second_preview36_physical and PHYSICAL_COMMIT in second_preview36_physical,
            "preview.36 physical MacBook receipt is not exact-package bound", failures)
    require("tsk_570cb03937f6" in second_preview36_physical
            and "run_gw_c8d2ad1aa845" in second_preview36_physical,
            "preview.36 physical marker or OpenClaw run evidence missing", failures)
    require("zero external-write PreparedActions" in normalized_second_preview36_physical
            and "protected Dashboard request returned HTTP 401" in normalized_second_preview36_physical,
            "preview.36 physical safety/logout proof missing", failures)
    require("## Preview 35 MacBook Client Staging" in second,
            "physical MacBook preview.35 staging receipt missing", failures)
    require(LEGACY_PHYSICAL_TAG in second and LEGACY_PHYSICAL_COMMIT in second,
            "physical MacBook receipt must bind the exact preview.35 package", failures)
    require("all returned HTTP 401" in normalized_second and "task count remained 25 before and after" in normalized_second,
            "physical MacBook anonymous denial receipt missing", failures)
    require("Tailscale Serve remained the transport and Funnel stayed disabled" in normalized_second,
            "advanced transport boundary missing from MacBook receipt", failures)
    require("## Preview 35 Authenticated MacBook Evidence" in second,
            "authenticated physical MacBook evidence section missing", failures)
    require("run_gw_edfe2753846f" in second and "phr_c2ea51dd3d37a09055e20889" in second,
            "authenticated MacBook run/receipt evidence missing", failures)
    require("disconnect/reconnect passed: true" in normalized_second and "logout denial passed: true" in normalized_second,
            "physical browser disconnect or logout-denial evidence missing", failures)
    require("70bae606c577191041778a92e3480138f3b67795" in second_preview35_physical
            and "preview.36 packages the fix" in normalized_second_preview35_physical
            and "closes the exact-package physical retest" in normalized_second_preview35_physical,
            "preview.35 marker defect history or preview.36 closure is missing", failures)
    require("overall second-device protocol remains partial" in normalized_second,
            "advanced receipt must not claim ordinary browser-only acceptance", failures)

    require(PHYSICAL_TAG in service_preview36 and PHYSICAL_COMMIT in service_preview36,
            "preview.36 service-upgrade receipt is not exact-package bound", failures)
    require("no-repository install/start/status/stop" in normalized_service_preview36,
            "preview.36 no-repository release receipt missing", failures)
    require("preserved Host data" in normalized_service_preview36
            and "two execution-capacity lanes" in normalized_service_preview36,
            "preview.36 data/Worker recovery receipt missing", failures)
    require("Funnel disabled" in normalized_service_preview36,
            "preview.36 upgrade must preserve the private transport boundary", failures)

    require(LEGACY_PHYSICAL_TAG in runtime and LEGACY_PHYSICAL_COMMIT in runtime,
            "preview.35 Runtime receipt is not exact-package bound", failures)
    require("run_gw_45eac4968e30" in runtime and "run_gw_7ac27edaf52c" in runtime,
            "fresh preview.35 OpenClaw/Hermes run evidence missing", failures)
    require("ap_customer_worker_delivery_run_gw_7ac27edaf52c` remains `pending`" in runtime,
            "Hermes Human Approval Wall state is no longer explicit", failures)
    require("No raw prompt, raw response, credential, private message, full transcript or database content was retained" in normalized_runtime,
            "preview.35 Runtime privacy boundary missing", failures)
    require(TAG in runtime_preview39 and COMMIT in runtime_preview39,
            "preview.39 Runtime receipt is not exact-package bound", failures)
    require("run_gw_0dfd8981a340" in runtime_preview39
            and "run_gw_a46813718b06" in runtime_preview39
            and "Four bounded Fleet samples spanning 105 seconds" in normalized_runtime_preview39,
            "preview.39 Hermes/OpenClaw persistent Runtime evidence is incomplete", failures)
    require("Agent Plans scored 100" in normalized_runtime_preview39
            and "zero Approval and zero Prepared Action" in normalized_runtime_preview39
            and "physical MacBook current-package browser acceptance" in normalized_runtime_preview39,
            "preview.39 bounded evidence or physical boundary missing", failures)
    require(PREVIOUS_TAG in runtime_preview38 and PREVIOUS_COMMIT in runtime_preview38,
            "preview.38 Runtime receipt is not exact-package bound", failures)
    require("run_gw_c835b4dab9a9" in runtime_preview38
            and "run_gw_be0e8275670f" in runtime_preview38
            and "pem_da01eaeea65a518a" in runtime_preview38
            and "pem_16c802e45dcbd6d9" in runtime_preview38,
            "preview.38 Hermes/OpenClaw Runtime evidence is incomplete", failures)
    require("15 Runtime Events" in normalized_runtime_preview38
            and "12 bounded Audit rows" in normalized_runtime_preview38
            and "does not claim that a human accepted either delivery" in normalized_runtime_preview38,
            "preview.38 bounded evidence or Human decision boundary missing", failures)
    require("current-package authenticated physical-browser review receipt" in normalized_runtime_preview38,
            "preview.38 physical browser review boundary missing", failures)
    require("does not repeat the preview.35 disconnect/reconnect flow" in normalized_runtime_preview38,
            "preview.38 must not synthesize current-package disconnect evidence", failures)
    require(PHYSICAL_TAG in runtime_preview36_host and PHYSICAL_COMMIT in runtime_preview36_host,
            "preview.36 Runtime receipt is not exact-package bound", failures)
    require("run_gw_ed42f579d487" in runtime_preview36_host
            and "pem_e1b9275c986daf4b" in runtime_preview36_host,
            "fresh preview.36 OpenClaw negated-intent evidence missing", failures)
    require("ap_customer_worker_delivery_run_gw_ed42f579d487` remains `pending`" in runtime_preview36_host,
            "preview.36 delivery decision boundary is no longer explicit", failures)
    require("zero PreparedActions" in normalized_runtime_preview36_host,
            "preview.36 negated external-write proof missing", failures)
    require("## Exact-Package Preview 36 Physical MacBook Result" in runtime,
            "preview.36 physical Runtime receipt missing", failures)
    require(PHYSICAL_TAG in runtime_preview36_physical and PHYSICAL_COMMIT in runtime_preview36_physical,
            "preview.36 physical Runtime receipt is not exact-package bound", failures)
    require("wfjob_9940b1e6ea15" in runtime_preview36_physical
            and "run_gw_c8d2ad1aa845" in runtime_preview36_physical
            and "pem_094a19932cdcc50e" in runtime_preview36_physical,
            "preview.36 physical OpenClaw evidence is incomplete", failures)
    require("ap_customer_worker_delivery_run_gw_c8d2ad1aa845` remains `pending`" in runtime_preview36_physical,
            "preview.36 physical delivery decision boundary is no longer explicit", failures)

    output = {
        "operation": "private_host_rc_status_smoke",
        "ok": not failures,
        "version": VERSION,
        "tag": TAG,
        "exact_commit": COMMIT,
        "checksums_recorded": len(CHECKSUMS),
        "local_receipts": [
            "preview39_release_asset_install",
            "preview39_service_upgrade_migration",
            "preview39_sustained_worker_session_observation",
            "preview39_hermes_persistent_real_runtime",
            "preview39_openclaw_persistent_real_runtime",
            "preview39_authenticated_human_service_receipt",
            "preview38_release_asset_install",
            "preview38_service_upgrade_migration",
            "preview38_worker_heartbeat_initial_readback",
            "worker_session_bound_observation_source_fix",
            "preview38_hermes_real_runtime",
            "preview38_openclaw_real_runtime",
            "preview38_physical_macbook_https_reachability",
            "preview37_release_asset_install",
            "preview37_service_upgrade_migration",
            "preview37_launchd_convergence",
            "preview36_real_runtime_evidence",
            "preview36_negated_read_only_runtime",
            "physical_macbook_anonymous_boundary",
            "physical_macbook_authenticated_workflow",
            "preview36_physical_marker",
            "preview36_physical_openclaw_runtime",
            "physical_browser_disconnect_reconnect",
            "approved_artifact_and_host_receipt_download",
            "physical_logout_denial",
        ],
        "external_gates_open": list(open_gate_markers),
        "failures": failures,
        "safety": {
            "network_used": False,
            "physical_evidence_synthesized": False,
            "raw_content_omitted": True,
            "token_omitted": True,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
