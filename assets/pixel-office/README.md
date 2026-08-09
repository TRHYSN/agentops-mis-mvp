# AgentOps MIS Pixel Office Asset Pack

This directory records the first-party visual asset boundary for the production
Pixel Office. The current pack is source-rendered rather than bitmap-based:
React components draw rooms, terrain, props, agents, task cards, effects, and
the HUD from project-owned geometry, palettes, and materials at runtime.

The authoritative source inventory is `asset-manifest.json`. Its ownership
claim is limited to the custom visual primitives, composition, geometry,
palettes, and materials authored for this project. The manifest separately lists
the local source dependency closure and the React/Lucide runtime code
dependencies; those package dependencies retain their own licenses and are not
claimed as project-owned artwork.

No Star-Office-UI, LimeZu, marketplace, paid tileset, reference-image, sprite,
font, or other third-party art is included. A later bitmap or sprite expansion
must add creator notes, source files, export hashes, and an explicit license
before it can replace any source-rendered slot.
