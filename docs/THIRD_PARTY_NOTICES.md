# Third-Party Notices

This document is release evidence for the local AgentOps MIS MVP. It is not
legal advice and does not replace a final legal review before public or
commercial distribution.

## First-Party Code

| Component | Location | License posture |
|---|---|---|
| AgentOps MIS backend, CLI and local UI | Repository root, `agentops_mis_cli/`, `server.py`, `ui/start-building-app/src/` | Proprietary local MVP; see root `LICENSE`. |
| Pixel Office operating map | `assets/pixel-office/`, `ui/start-building-app/src/app/components/pixel/` and `PixelOffice.tsx` | Project-owned source-rendered asset pack; no copied bitmap, sprite, tileset or Star-Office art assets. |

## Runtime And UI Dependencies

Package-manager metadata is authoritative for exact versions:

- Python package metadata: `pyproject.toml`
- npm package metadata: `ui/start-building-app/package.json`
- npm lockfile: `ui/start-building-app/package-lock.json`

Direct UI dependencies include React ecosystem packages, Radix UI primitives,
MUI, Emotion, Lucide React, Vite/Tailwind build tooling, Recharts, date-fns,
Motion, and related UI utilities. Their upstream packages retain their own
licenses and notices.

## Reference Projects

The following projects are research/reference inputs only. They are not
vendored into the product source tree and must not be treated as AgentOps MIS
source or assets.

| Reference | Use | Boundary |
|---|---|---|
| Star-Office-UI | Optional local legacy visualizer and design reference | Code may be MIT, but art assets are non-commercial/reference-only. No Star-Office art is copied into the product UI. |
| openclaw-virtual-office | Presence/state-mapping reference | Concepts only; no assets copied. |
| pixel-agents family | Future movement/canvas reference | Concepts only; paid or unclear tilesets are excluded. |

## Pixel Office Asset Boundary

Commercial or public release is blocked unless the release artifact proves:

- no Star-Office-UI art, sprites, furniture, backgrounds, posters or animation
  frames are bundled;
- no paid tileset, LimeZu-derived art, Donarg tileset, MetroCity-style sprite or
  unclear-license asset is bundled;
- the current `assets/pixel-office/` pack retains its own `LICENSE.md`, source
  manifest, and zero third-party entries;
- product copy does not imply third-party art is owned by AgentOps MIS.

The current local UI uses the project-owned source-rendered pack: CSS geometry,
component markup, palettes, materials, and state-driven visual effects only.
