#!/usr/bin/env python3
"""Verify local MVP license/provenance evidence and Pixel Office asset boundary."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui" / "start-building-app"
PIXEL_ASSET_ROOT = ROOT / "assets" / "pixel-office"
PIXEL_ASSET_MANIFEST = PIXEL_ASSET_ROOT / "asset-manifest.json"
PIXEL_ART_KIT_MANIFEST = (
    UI_DIR / "src" / "app" / "spatial" / "manifests" / "warm-research-art-kit.v0.json"
)
PRODUCT_ASSET_ROOTS = [
    UI_DIR / "src",
    UI_DIR / "public",
]
REQUIRED_DOCS = [
    ROOT / "LICENSE",
    ROOT / "docs" / "THIRD_PARTY_NOTICES.md",
    ROOT / "docs" / "RELEASE_PROVENANCE.md",
    ROOT / "docs" / "SBOM_MINIMAL.md",
    ROOT / "docs" / "PIXEL_OFFICE_REFERENCE_AUDIT.md",
    ROOT / "docs" / "PIXEL_OFFICE_ASSET_REPLACEMENT_PLAN.md",
    PIXEL_ASSET_ROOT / "README.md",
    PIXEL_ASSET_ROOT / "LICENSE.md",
    PIXEL_ASSET_MANIFEST,
]
ASSET_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".aseprite",
    ".tmx",
}
FORBIDDEN_PRODUCT_MARKERS = [
    "Star-Office-UI/assets",
    "LimeZu",
    "Donarg",
    "MetroCity",
    "sprite sheet",
    "tile atlas",
]
SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"agtok_[A-Za-z0-9_]+"),
    re.compile(r"agtsess_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ntn_[A-Za-z0-9]{8,}"),
]
LOCAL_IMPORT = re.compile(
    r"""(?:\bfrom\s+|\bimport\s*\(\s*|^\s*import\s+)["'](\.[^"']+)["']"""
)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def product_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCT_ASSET_ROOTS:
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(files)


def resolve_local_import(source: Path, specifier: str) -> Path | None:
    base = (source.parent / specifier).resolve()
    candidates = [base] if base.suffix else [
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base / "index.ts",
        base / "index.tsx",
        base / "index.js",
        base / "index.jsx",
    ]
    for candidate in candidates:
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def local_import_closure(source_paths: set[str]) -> set[str]:
    closure = set(source_paths)
    pending = list(source_paths)
    while pending:
        relative_source = pending.pop()
        source = ROOT / relative_source
        for line in read(source).splitlines():
            for specifier in LOCAL_IMPORT.findall(line):
                dependency = resolve_local_import(source, specifier)
                if dependency is None:
                    raise ValueError(
                        f"unresolved local import in {relative_source}: {specifier}"
                    )
                relative_dependency = dependency.relative_to(ROOT).as_posix()
                if relative_dependency not in closure:
                    closure.add(relative_dependency)
                    pending.append(relative_dependency)
    return closure


def main() -> int:
    failures: list[str] = []
    for path in REQUIRED_DOCS:
        require(path.exists(), f"missing required provenance document: {path.relative_to(ROOT)}", failures)

    license_text = read(ROOT / "LICENSE") if (ROOT / "LICENSE").exists() else ""
    pyproject = read(ROOT / "pyproject.toml")
    ui_package = json.loads(read(UI_DIR / "package.json"))
    ui_lock = json.loads(read(UI_DIR / "package-lock.json"))
    notices = read(ROOT / "docs" / "THIRD_PARTY_NOTICES.md") if (ROOT / "docs" / "THIRD_PARTY_NOTICES.md").exists() else ""
    provenance = read(ROOT / "docs" / "RELEASE_PROVENANCE.md") if (ROOT / "docs" / "RELEASE_PROVENANCE.md").exists() else ""
    sbom = read(ROOT / "docs" / "SBOM_MINIMAL.md") if (ROOT / "docs" / "SBOM_MINIMAL.md").exists() else ""
    replacement = read(ROOT / "docs" / "PIXEL_OFFICE_ASSET_REPLACEMENT_PLAN.md")
    reference_audit = read(ROOT / "docs" / "PIXEL_OFFICE_REFERENCE_AUDIT.md")
    asset_readme = read(PIXEL_ASSET_ROOT / "README.md")
    asset_license = read(PIXEL_ASSET_ROOT / "LICENSE.md")
    asset_manifest = json.loads(read(PIXEL_ASSET_MANIFEST))
    art_kit_manifest = json.loads(read(PIXEL_ART_KIT_MANIFEST))

    require("Proprietary Local MVP" in license_text or "Proprietary local MVP" in license_text, "root LICENSE does not declare local MVP posture", failures)
    require("All rights reserved" in license_text, "root LICENSE missing all-rights-reserved boundary", failures)
    require('license = { text = "Proprietary local MVP" }' in pyproject, "pyproject license metadata is not aligned", failures)
    require(ui_package.get("private") is True, "UI package must remain private", failures)
    require(ui_package.get("license") == "UNLICENSED", "UI package license must be UNLICENSED", failures)
    require((ui_lock.get("packages") or {}).get("", {}).get("license") == "UNLICENSED", "UI lockfile root license must be UNLICENSED", failures)

    required_phrases = [
        "Star-Office-UI",
        "non-commercial",
        "No Star-Office art is copied",
        "Package-manager metadata is authoritative",
    ]
    for phrase in required_phrases:
        require(phrase in notices, f"third-party notices missing phrase: {phrase}", failures)

    for phrase in [
        "VITE_STAR_OFFICE_URL",
        "Commercial Build Exclusion",
        "does not use copied Star-Office-UI art assets",
        "assets/pixel-office/",
    ]:
        require(phrase in provenance, f"release provenance missing phrase: {phrase}", failures)

    direct_deps = sorted((ui_package.get("dependencies") or {}).items())
    direct_dev_deps = sorted((ui_package.get("devDependencies") or {}).items())
    pinned_ui_packages = {
        **(ui_package.get("dependencies") or {}),
        **(ui_package.get("devDependencies") or {}),
        **(ui_package.get("peerDependencies") or {}),
    }
    for name, version in direct_deps + direct_dev_deps:
        require(f"| {name} | {version} |" in sbom, f"minimal SBOM missing direct npm package: {name}@{version}", failures)
    require("agentops-mis-cli | 0.1.0" in sbom, "minimal SBOM missing CLI component", failures)
    require(
        "zero third-party assets and no bitmap" in sbom,
        "minimal SBOM missing first-party source-rendered asset boundary",
        failures,
    )

    require("Code-rendered commercial pack: complete" in replacement, "asset replacement plan does not close the code-rendered pack", failures)
    require("Do not copy Star-Office art" in reference_audit, "reference audit missing Star-Office copy boundary", failures)

    require("source-rendered" in asset_readme, "Pixel Office asset README missing source-rendered boundary", failures)
    require("All rights reserved" in asset_license, "Pixel Office asset license missing ownership boundary", failures)
    require(asset_manifest.get("schemaVersion") == "agentops-pixel-office-asset-pack/v1", "Pixel Office asset manifest schema mismatch", failures)
    require(asset_manifest.get("provenance") == "first_party", "Pixel Office asset pack must be first-party", failures)
    require(asset_manifest.get("license") == "PROJECT_OWNED", "Pixel Office asset pack license mismatch", failures)
    require(asset_manifest.get("distribution") == "source_rendered", "Pixel Office asset pack distribution mismatch", failures)
    require(
        asset_manifest.get("ownershipScope") == "custom_visual_primitives_only",
        "Pixel Office ownership scope must be limited to custom visual primitives",
        failures,
    )
    require(asset_manifest.get("thirdPartyAssets") == [], "Pixel Office asset pack must not include third-party assets", failures)
    runtime_dependencies = asset_manifest.get("runtimeCodeDependencies") or []
    runtime_dependency_names = {
        str(entry.get("package") or "")
        for entry in runtime_dependencies
        if isinstance(entry, dict)
    }
    require(
        runtime_dependency_names == {"react", "lucide-react"},
        "Pixel Office runtime code dependency boundary is incomplete",
        failures,
    )
    for entry in runtime_dependencies:
        if not isinstance(entry, dict):
            continue
        package = str(entry.get("package") or "")
        require(package in pinned_ui_packages, f"Pixel Office runtime dependency is not pinned: {package}", failures)
        require(
            str(entry.get("classification") or "").endswith("_not_art_asset"),
            f"Pixel Office dependency classification is ambiguous: {package}",
            failures,
        )

    source_entries = asset_manifest.get("sources") or []
    require(isinstance(source_entries, list) and source_entries, "Pixel Office asset source inventory is empty", failures)
    declared_sources = {
        str(entry.get("path") or "")
        for entry in source_entries
        if isinstance(entry, dict)
    }
    for source_path in declared_sources:
        require(bool(source_path) and (ROOT / source_path).is_file(), f"Pixel Office asset source missing: {source_path}", failures)
    dependency_closure = asset_manifest.get("sourceDependencyClosure") or []
    require(
        isinstance(dependency_closure, list) and dependency_closure,
        "Pixel Office source dependency closure is empty",
        failures,
    )
    for source_path in dependency_closure:
        require(
            isinstance(source_path, str) and (ROOT / source_path).is_file(),
            f"Pixel Office dependency closure source missing: {source_path}",
            failures,
        )
    require(
        declared_sources.issubset(set(dependency_closure)),
        "Pixel Office authored sources are not closed by the dependency inventory",
        failures,
    )
    try:
        derived_dependency_closure = local_import_closure(declared_sources)
    except ValueError as error:
        derived_dependency_closure = set()
        failures.append(str(error))
    require(
        set(dependency_closure) == derived_dependency_closure,
        "Pixel Office source dependency closure differs from recursive local imports: "
        f"missing={sorted(derived_dependency_closure - set(dependency_closure))} "
        f"extra={sorted(set(dependency_closure) - derived_dependency_closure)}",
        failures,
    )

    art_slots = art_kit_manifest.get("assetSlots") or []
    require(isinstance(art_slots, list) and len(art_slots) >= 5, "Pixel Office art kit slots are incomplete", failures)
    for slot in art_slots:
        require(isinstance(slot, dict), "Pixel Office art kit slot must be an object", failures)
        if not isinstance(slot, dict):
            continue
        slot_id = str(slot.get("id") or "unknown")
        require(slot.get("status") == "ready", f"Pixel Office asset slot is not ready: {slot_id}", failures)
        require(slot.get("provenance") == "first_party", f"Pixel Office asset slot provenance mismatch: {slot_id}", failures)
        require(slot.get("license") == "PROJECT_OWNED", f"Pixel Office asset slot license mismatch: {slot_id}", failures)
        source_path = str(slot.get("sourcePath") or "")
        require(bool(source_path) and (UI_DIR / source_path).is_file(), f"Pixel Office asset slot source missing: {slot_id}", failures)
        repo_source = (Path("ui/start-building-app") / source_path).as_posix()
        require(repo_source in declared_sources, f"Pixel Office asset slot absent from source inventory: {slot_id}", failures)

    files = product_files()
    asset_like_paths = [path.relative_to(ROOT).as_posix() for path in files if path.suffix.lower() in ASSET_SUFFIXES]
    require(not asset_like_paths, f"product source must not contain Pixel Office bitmap/sprite/tile assets: {asset_like_paths}", failures)

    product_text_parts: list[str] = []
    for path in files:
        if path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx", ".css", ".md", ".html", ".json"}:
            product_text_parts.append(read(path))
    product_text = "\n".join(product_text_parts)
    forbidden_hits = [marker for marker in FORBIDDEN_PRODUCT_MARKERS if marker in product_text]
    require(not forbidden_hits, f"forbidden commercial-asset marker found in product source: {forbidden_hits}", failures)
    require("VITE_STAR_OFFICE_URL" in product_text, "legacy Star Office link should remain explicit and optional", failures)
    require("Star-Office-UI/assets" not in product_text, "product source references Star-Office asset path", failures)

    evidence_bundle = "\n".join([license_text, notices, provenance, sbom])
    secret_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(evidence_bundle)]
    require(not secret_hits, f"secret-like marker found in license/provenance evidence: {secret_hits}", failures)

    output = {
        "ok": not failures,
        "operation": "license_provenance_smoke",
        "documents": [str(path.relative_to(ROOT)) for path in REQUIRED_DOCS],
        "ui_direct_dependencies": len(direct_deps),
        "ui_direct_dev_dependencies": len(direct_dev_deps),
        "pixel_office_product_assets": asset_like_paths,
        "pixel_office_asset_pack": {
            "id": asset_manifest.get("id"),
            "version": asset_manifest.get("version"),
            "distribution": asset_manifest.get("distribution"),
            "ready_slots": len([slot for slot in art_slots if isinstance(slot, dict) and slot.get("status") == "ready"]),
            "third_party_assets": len(asset_manifest.get("thirdPartyAssets") or []),
            "runtime_code_dependencies": sorted(runtime_dependency_names),
            "source_dependency_files": len(dependency_closure),
        },
        "contract": "Local MVP license, third-party notices, minimal SBOM, release provenance, and a ready first-party source-rendered Pixel Office asset pack are present.",
        "failures": failures,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
