# Release Provenance

## Scope

This file records provenance for the local AgentOps MIS MVP release evidence
packet. It focuses on code, package metadata, Pixel Office visuals and
commercial-build exclusions.

## First-Party Sources

| Area | Source | Evidence |
|---|---|---|
| Backend/API | `server.py` | Local MIS ledger, governance APIs and workflow endpoints. |
| CLI/worker | `agentops_mis_cli/` | Installable `agentops` and `agentops-worker` entrypoints. |
| UI | `ui/start-building-app/src/` | Vite/React local UI. |
| Pixel Office | `assets/pixel-office/`, `ui/start-building-app/src/app/components/pages/PixelOffice.tsx`, `ui/start-building-app/src/app/components/pixel/` | Project-owned source-rendered asset pack, native visualizer, and customer dispatch entry. |
| Knowledge/runbooks | `knowledge/`, `docs/` | Human/agent-readable doctrine, bases and runbooks. |

## Pixel Office Provenance

The production Pixel Office surface is a first-party implementation. It uses:

- React components;
- CSS borders, gradients, grid textures and absolute positioning;
- Lucide icons through package dependencies;
- MIS API data from the local AgentOps backend.

`assets/pixel-office/asset-manifest.json` binds the production art-kit slots to
their committed source files and declares the local source dependency closure.
`assets/pixel-office/LICENSE.md` covers only the project-authored custom visual
primitives, composition, geometry, palettes, and materials. React and Lucide
remain separately licensed runtime code dependencies and are not represented as
project-owned artwork. The pack is source-rendered and ships no bitmap sprite or
tileset payload.

It does not use copied Star-Office-UI art assets, paid tilesets, bitmap sprites,
third-party scene JSON, or external virtual-office engines.

Star-Office-UI is kept only as an optional legacy local link through
`VITE_STAR_OFFICE_URL`. The link is not enabled by default and is not a source
of authority for tasks, runs, approvals, memory, evaluations, artifacts or
audits.

## Commercial Build Exclusion

Commercial/public distribution is blocked unless the following stay true:

- Product source under `ui/start-building-app/src` and `ui/start-building-app/public`
  contains no copied `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.ico`,
  `.aseprite`, `.tmx`, `.tsx`, `.json` scene asset pack, sprite sheet or tile
  atlas for Pixel Office.
- `VITE_STAR_OFFICE_URL` remains an optional external local link only.
- The current `assets/pixel-office/` pack retains `LICENSE.md`, source notes,
  a complete manifest, no third-party entries, and only existing committed
  sources.
- Every production art-kit slot remains `ready`, `first_party`, and
  `PROJECT_OWNED`.
- Root `LICENSE`, `pyproject.toml`, UI `package.json`, this provenance file and
  `docs/THIRD_PARTY_NOTICES.md` agree that this repository is a proprietary
  local MVP unless a later written license overrides it.

## Verification

Run:

```bash
python3 scripts/license_provenance_smoke.py
python3 scripts/pixel_office_visualizer_boundary_smoke.py
```

These checks are release evidence, not a substitute for final legal review.
