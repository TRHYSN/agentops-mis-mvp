# Pixel Office Asset Replacement Plan

## Goal

Before any public commercial release, keep Pixel Office visuals entirely within
an original AgentOps MIS asset boundary.

The product should keep the useful interaction pattern:

- visual AI workforce floor
- runtime status map
- task zones
- approval gate
- audit vault
- connector dock

The production decision is a project-owned, source-rendered React/CSS asset
pack. It does not copy or bundle the temporary Star-Office-UI demo art.

## Current Production Decision

**Code-rendered commercial pack: complete.**

The current pack is recorded under `assets/pixel-office/` and implemented by
the committed Pixel Office components and theme sources. Rooms, terrain, props,
avatars, state effects, and HUD materials are rendered from source at runtime.
`asset-manifest.json` inventories those sources; `LICENSE.md` records ownership.

Transparent PNG sprites or a Figma kit are optional future enhancements, not a
dependency of the current commercial build. Adding them would create a new
asset review gate and must not weaken the source-rendered fallback.

## Original Asset Pack Scope

### Agent Roles

- Research Agent
- Coding Agent
- Reviewer Agent
- Ops Agent
- Memory Curator
- Connector Bot
- Audit Bot

### Work Areas

- Control Tower
- Agent Registry Desk
- Task Board Zone
- Runtime Lab
- Tool Room
- Approval Gate
- Memory Library
- Evaluation Room
- Audit Vault
- External Base Dock

### State Animations

- idle
- planning
- researching
- writing
- coding
- executing
- waiting_approval
- evaluating
- syncing
- auditing
- error

### Connector Icons

- OpenClaw
- Hermes
- Agnesfallback
- Notion
- W&B
- Plane
- Docmost
- Mattermost
- n8n

## Optional Bitmap Expansion

- 32px or 48px tile grid.
- Isometric or top-down pixel office, but choose one perspective and keep it consistent.
- Limited palette with clear status colors:
  - green: healthy/running
  - yellow: waiting approval
  - blue: syncing/evaluating
  - red: error
  - gray: idle
- Each agent role should have a distinct silhouette, not only a color swap.
- Status animation should be readable even when compressed in a video.

## Current Directory

```text
assets/pixel-office/
  README.md
  LICENSE.md
  asset-manifest.json
```

## Optional Figma / Generation Prompt Starter

```text
Create an original pixel art office asset pack for an AI workforce management system named AgentOps MIS.
Style: clean 32px tile pixel art, consistent top-down office perspective, no copied third-party assets.
Rooms: Control Tower, Agent Registry Desk, Task Board Zone, Runtime Lab, Tool Room, Approval Gate, Memory Library, Evaluation Room, Audit Vault, External Base Dock.
Characters: Research Agent, Coding Agent, Reviewer Agent, Ops Agent, Memory Curator, Connector Bot, Audit Bot.
States: idle, researching, writing, coding, executing, waiting_approval, evaluating, syncing, auditing, error.
Export transparent PNG sprites and Figma components with clear naming.
```

## Replacement Milestones

1. Complete: keep Star-Office-UI as an optional local non-commercial link only.
2. Complete: freeze the AgentOps MIS state vocabulary and scene layout.
3. Complete: implement original rooms, agents, states, effects, and HUD in
   project-owned React/CSS source.
4. Complete: inventory the production source-rendered pack in
   `assets/pixel-office/asset-manifest.json`.
5. Complete: verify public-facing builds import no Star-Office or third-party
   Pixel Office art.
6. Complete: record original ownership and attribution boundaries.
7. Optional: add reviewed Figma/bitmap exports without replacing the
   source-rendered fallback.

## Release Gate

Public commercial release is blocked until:

- no Star-Office-UI art asset remains in product bundles
- no LimeZu-derived art asset remains unless its commercial license is explicitly satisfied
- `assets/pixel-office/LICENSE.md` states original ownership/licensing
- every production art-kit slot is `ready`, `first_party`, and points to a
  committed source listed in `asset-manifest.json`
- product README and marketing pages no longer imply Star-Office assets are ours

The current source-rendered pack satisfies these repository gates. Final legal
review remains an external release decision, not a software fallback to
third-party art.
