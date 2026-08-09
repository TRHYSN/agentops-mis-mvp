#!/usr/bin/env python3
"""Dependency-free contract smoke for the Spatial OS foundation v0."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "ui" / "start-building-app"
SPATIAL_ROOT = ROOT / "ui" / "start-building-app" / "src" / "app" / "spatial"
MANIFEST_ROOT = SPATIAL_ROOT / "manifests"
APP_PATH = ROOT / "ui" / "start-building-app" / "src" / "app" / "App.tsx"
PIXEL_OFFICE_PATH = (
    ROOT
    / "ui"
    / "start-building-app"
    / "src"
    / "app"
    / "components"
    / "pages"
    / "PixelOffice.tsx"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(name: str) -> dict[str, Any]:
    path = MANIFEST_ROOT / name
    require(path.is_file(), f"missing manifest: {path.relative_to(ROOT)}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    require(isinstance(payload, dict), f"manifest must be an object: {name}")
    return payload


def validate_template(template: dict[str, Any]) -> None:
    require(template.get("schemaVersion") == "spatial-world-template/v0", "invalid world template schema")
    require(template.get("id") == "top-down-rpg-campus", "unexpected world template id")
    require(template.get("projection") == "top-down", "first advanced template must be top-down")
    require(template.get("rendererRequirements", {}).get("fallback") == "basic-lite", "Basic/Lite fallback missing")
    require(template.get("rendererRequirements", {}).get("preferred") == "game-canvas", "advanced renderer must prefer game canvas")

    stages = template.get("semanticZoom")
    require(isinstance(stages, list) and len(stages) == 4, "semantic zoom must define four stages")
    require([stage.get("level") for stage in stages] == [0, 1, 2, 3], "semantic zoom levels must be 0..3")
    require(
        [stage.get("queryScope") for stage in stages]
        == ["global", "district", "facility", "workspace"],
        "semantic zoom must deepen query scope",
    )

    capabilities = template.get("capabilities", {})
    for capability in (
        "tilemap",
        "interiors",
        "semanticZoom",
        "agentPathfinding",
        "animatedAvatars",
        "minimap",
        "screenshotHooks",
    ):
        require(capabilities.get(capability) is True, f"required capability disabled: {capability}")


def validate_art_kit(art_kit: dict[str, Any]) -> None:
    require(art_kit.get("schemaVersion") == "spatial-art-kit/v0", "invalid art kit schema")
    require(art_kit.get("id") == "warm-research-v0", "unexpected art kit id")
    require(art_kit.get("visualLanguage", {}).get("perspective") == "top-down", "art perspective mismatch")
    require(art_kit.get("pixelDensity", {}).get("scalePolicy") == "integer-only", "pixel art must use integer scaling")

    required_animations = {
        "idle-north",
        "idle-east",
        "idle-south",
        "idle-west",
        "walk-north",
        "walk-east",
        "walk-south",
        "walk-west",
        "read",
        "type",
        "carry",
        "wait",
        "blocked",
        "complete",
    }
    require(required_animations <= set(art_kit.get("requiredAgentAnimations", [])), "agent animation contract incomplete")

    assets = art_kit.get("assetSlots")
    require(isinstance(assets, list), "assetSlots must be a list")
    required_kinds = {
        "terrain-tileset",
        "building-tileset",
        "interior-tileset",
        "prop-atlas",
        "avatar-atlas",
        "effect-atlas",
        "hud-skin",
    }
    require(required_kinds <= {asset.get("kind") for asset in assets}, "art kit does not cover a complete game-art module")

    for asset in assets:
        require(asset.get("status") == "ready", f"commercial asset slot is not ready: {asset.get('id')}")
        require(
            asset.get("provenance") == "first_party",
            f"forbidden asset provenance: {asset.get('id')}",
        )
        require(asset.get("license") == "PROJECT_OWNED", f"forbidden asset license: {asset.get('id')}")
        source_path = str(asset.get("sourcePath", ""))
        require(bool(source_path), f"ready asset source missing: {asset.get('id')}")
        require(not re.match(r"https?://", source_path, flags=re.IGNORECASE), f"remote asset URL forbidden: {asset.get('id')}")
        require((UI_ROOT / source_path).is_file(), f"ready asset source does not exist: {asset.get('id')}")

    serialized = json.dumps(art_kit, ensure_ascii=False).lower()
    for forbidden_reference in ("stardew", "star-office", "envato", "paid tileset"):
        require(forbidden_reference not in serialized, f"commercial/reference asset name leaked into manifest: {forbidden_reference}")


def validate_world(world: dict[str, Any], template: dict[str, Any], art_kit: dict[str, Any]) -> None:
    require(world.get("schemaVersion") == "spatial-world/v0", "invalid world schema")
    require(world.get("templateId") == template.get("id"), "world/template mismatch")
    require(world.get("artKitId") == art_kit.get("id"), "world/art-kit mismatch")
    require(template.get("defaultWorldId") == world.get("id"), "template default world mismatch")
    require(art_kit.get("id") in template.get("supportedArtKitIds", []), "template does not support art kit")
    require(template.get("id") in art_kit.get("compatibleWorldTemplateIds", []), "art kit does not support template")

    nodes = world.get("nodes")
    require(isinstance(nodes, list) and nodes, "world nodes must be non-empty")
    node_by_id = {node.get("id"): node for node in nodes}
    require(len(node_by_id) == len(nodes), "world node ids must be unique")
    root_id = world.get("rootNodeId")
    require(root_id in node_by_id, "root node missing")
    require(node_by_id[root_id].get("kind") == "world", "root node must be world")

    required_chain = [
        "world.agentops-atlas",
        "district.research",
        "facility.ai-papers-house",
        "workspace.claude-research-desk",
        "portal.claude-run-ledger",
    ]
    for node_id in required_chain:
        require(node_id in node_by_id, f"vertical-slice node missing: {node_id}")
    for parent_id, child_id in zip(required_chain, required_chain[1:]):
        require(node_by_id[child_id].get("parentId") == parent_id, f"invalid vertical-slice ancestry: {child_id}")
        require(child_id in node_by_id[parent_id].get("childIds", []), f"parent does not list child: {parent_id} -> {child_id}")

    for node in nodes:
        node_id = node.get("id")
        zoom_level = node.get("zoomLevel")
        require(isinstance(zoom_level, int) and 0 <= zoom_level <= 3, f"invalid zoom level: {node_id}")
        parent_id = node.get("parentId")
        if parent_id is not None:
            require(parent_id in node_by_id, f"missing parent {parent_id} for {node_id}")
            require(node_id in node_by_id[parent_id].get("childIds", []), f"parent/child mismatch for {node_id}")
        for child_id in node.get("childIds", []):
            require(child_id in node_by_id, f"missing child {child_id} for {node_id}")
            require(node_by_id[child_id].get("parentId") == node_id, f"child/parent mismatch for {child_id}")

    app_source = APP_PATH.read_text(encoding="utf-8")
    formal_routes = set(re.findall(r'<Route\s+path="([^"]+)"', app_source))
    portals = world.get("portals")
    require(isinstance(portals, list) and portals, "world portals must be non-empty")
    portal_ids: set[str] = set()
    for portal in portals:
        portal_id = portal.get("id")
        require(isinstance(portal_id, str) and portal_id, "portal id missing")
        require(portal_id not in portal_ids, f"duplicate portal id: {portal_id}")
        portal_ids.add(portal_id)
        node_id = portal.get("nodeId")
        require(node_by_id.get(node_id, {}).get("kind") == "portal", f"portal node missing: {portal_id}")
        authority = portal.get("authorityRef", {})
        require(authority.get("authority") == "agentops-mis", f"portal transferred authority: {portal_id}")
        route = authority.get("route")
        require(route in formal_routes, f"portal route is not registered in App.tsx: {route}")
        require(portal.get("confirmationRequired") is False, f"read-only navigation should not require action confirmation: {portal_id}")

    for node in nodes:
        primary_portal = node.get("primaryPortalId")
        if primary_portal:
            require(primary_portal in portal_ids, f"node has missing primary portal: {node.get('id')}")


def validate_bridge() -> None:
    projection_source = (SPATIAL_ROOT / "basicPixelProjection.ts").read_text(encoding="utf-8")
    require("SpatialProjectionAdapter" in projection_source, "Basic bridge does not implement projection contract")
    require("authority: \"agentops-mis\"" in projection_source, "Basic bridge authority marker missing")
    require("PixelAgent" in projection_source and "PixelTaskCard" in projection_source, "Basic Pixel types are not projected")

    page_source = PIXEL_OFFICE_PATH.read_text(encoding="utf-8")
    require("basicPixelProjectionAdapter.project" in page_source, "Pixel Office does not exercise the projection bridge")
    require('data-spatial-renderer="basic-lite"' in page_source, "Basic/Lite renderer marker missing")
    require("data-spatial-template" in page_source, "world template marker missing")


def main() -> None:
    template = load_json("top-down-rpg-campus.v0.json")
    art_kit = load_json("warm-research-art-kit.v0.json")
    world = load_json("research-district.v0.json")
    validate_template(template)
    validate_art_kit(art_kit)
    validate_world(world, template, art_kit)
    validate_bridge()
    print(
        "Spatial OS manifest smoke passed: "
        f"template={template['id']} art_kit={art_kit['id']} world={world['id']} "
        f"nodes={len(world['nodes'])} portals={len(world['portals'])}"
    )


if __name__ == "__main__":
    main()
