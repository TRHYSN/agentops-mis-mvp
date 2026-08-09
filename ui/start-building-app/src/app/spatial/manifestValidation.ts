import type {
  ArtKitManifest,
  SemanticWorldNode,
  SpatialPortal,
  SpatialWorldManifest,
  WorldTemplateManifest,
} from "./contracts";

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`[Spatial OS manifest] ${message}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  invariant(isRecord(value), `${label} must be an object`);
  return value;
}

function requireString(value: unknown, label: string): string {
  invariant(typeof value === "string" && value.trim().length > 0, `${label} must be a non-empty string`);
  return value;
}

function requireNumber(value: unknown, label: string): number {
  invariant(typeof value === "number" && Number.isFinite(value), `${label} must be a finite number`);
  return value;
}

function requireBoolean(value: unknown, label: string): boolean {
  invariant(typeof value === "boolean", `${label} must be a boolean`);
  return value;
}

function requireArray(value: unknown, label: string): unknown[] {
  invariant(Array.isArray(value), `${label} must be an array`);
  return value;
}

function requireStringArray(value: unknown, label: string): string[] {
  return requireArray(value, label).map((item, index) => requireString(item, `${label}[${index}]`));
}

function validateLocalizedText(value: unknown, label: string) {
  const record = requireRecord(value, label);
  requireString(record.en, `${label}.en`);
  requireString(record.zh, `${label}.zh`);
}

function validateNode(value: unknown, index: number): SemanticWorldNode {
  const node = requireRecord(value, `nodes[${index}]`);
  requireString(node.id, `nodes[${index}].id`);
  requireString(node.kind, `nodes[${index}].kind`);
  validateLocalizedText(node.label, `nodes[${index}].label`);
  validateLocalizedText(node.description, `nodes[${index}].description`);
  invariant(node.parentId === null || typeof node.parentId === "string", `nodes[${index}].parentId must be null or string`);
  requireStringArray(node.childIds, `nodes[${index}].childIds`);
  const zoomLevel = requireNumber(node.zoomLevel, `nodes[${index}].zoomLevel`);
  invariant(Number.isInteger(zoomLevel) && zoomLevel >= 0 && zoomLevel <= 3, `nodes[${index}].zoomLevel must be 0..3`);
  requireStringArray(node.tags, `nodes[${index}].tags`);
  return node as unknown as SemanticWorldNode;
}

function validatePortal(value: unknown, index: number): SpatialPortal {
  const portal = requireRecord(value, `portals[${index}]`);
  requireString(portal.id, `portals[${index}].id`);
  requireString(portal.nodeId, `portals[${index}].nodeId`);
  validateLocalizedText(portal.label, `portals[${index}].label`);
  const authority = requireRecord(portal.authorityRef, `portals[${index}].authorityRef`);
  invariant(authority.authority === "agentops-mis", `portals[${index}] must preserve AgentOps MIS authority`);
  requireString(authority.kind, `portals[${index}].authorityRef.kind`);
  const route = requireString(authority.route, `portals[${index}].authorityRef.route`);
  invariant(route.startsWith("/"), `portals[${index}] route must be a formal local MIS route`);
  requireString(portal.interaction, `portals[${index}].interaction`);
  requireBoolean(portal.confirmationRequired, `portals[${index}].confirmationRequired`);
  return portal as unknown as SpatialPortal;
}

export function validateWorldTemplateManifest(value: unknown): WorldTemplateManifest {
  const manifest = requireRecord(value, "world template");
  invariant(manifest.schemaVersion === "spatial-world-template/v0", "unsupported world-template schemaVersion");
  requireString(manifest.id, "world template.id");
  requireString(manifest.version, "world template.version");
  validateLocalizedText(manifest.label, "world template.label");
  validateLocalizedText(manifest.description, "world template.description");
  invariant(["top-down", "isometric", "cutaway", "graph-space"].includes(String(manifest.projection)), "invalid projection");
  requireString(manifest.defaultWorldId, "world template.defaultWorldId");
  requireString(manifest.defaultArtKitId, "world template.defaultArtKitId");
  const supportedArtKitIds = requireStringArray(manifest.supportedArtKitIds, "world template.supportedArtKitIds");
  invariant(supportedArtKitIds.includes(String(manifest.defaultArtKitId)), "default art kit must be supported");

  const stages = requireArray(manifest.semanticZoom, "world template.semanticZoom");
  invariant(stages.length === 4, "semantic zoom must define exactly four levels for v0");
  const levels = stages.map((stage, index) => {
    const record = requireRecord(stage, `semanticZoom[${index}]`);
    const level = requireNumber(record.level, `semanticZoom[${index}].level`);
    requireString(record.id, `semanticZoom[${index}].id`);
    requireStringArray(record.nodeKinds, `semanticZoom[${index}].nodeKinds`);
    requireString(record.cameraMode, `semanticZoom[${index}].cameraMode`);
    requireString(record.queryScope, `semanticZoom[${index}].queryScope`);
    requireString(record.interactionDepth, `semanticZoom[${index}].interactionDepth`);
    return level;
  });
  invariant(levels.join(",") === "0,1,2,3", "semantic zoom levels must be ordered 0,1,2,3");

  const capabilities = requireRecord(manifest.capabilities, "world template.capabilities");
  ["tilemap", "interiors", "semanticZoom", "agentPathfinding", "animatedAvatars", "lighting", "weather", "minimap", "screenshotHooks"].forEach((key) => {
    requireBoolean(capabilities[key], `world template.capabilities.${key}`);
  });
  invariant(capabilities.semanticZoom === true, "Advanced world templates must support semantic zoom");

  const renderer = requireRecord(manifest.rendererRequirements, "world template.rendererRequirements");
  invariant(renderer.preferred === "game-canvas" || renderer.preferred === "dom", "invalid preferred renderer");
  invariant(renderer.fallback === "basic-lite", "fallback renderer must remain Basic / Lite");

  return manifest as unknown as WorldTemplateManifest;
}

export function validateArtKitManifest(value: unknown): ArtKitManifest {
  const manifest = requireRecord(value, "art kit");
  invariant(manifest.schemaVersion === "spatial-art-kit/v0", "unsupported art-kit schemaVersion");
  requireString(manifest.id, "art kit.id");
  requireString(manifest.version, "art kit.version");
  validateLocalizedText(manifest.label, "art kit.label");
  validateLocalizedText(manifest.description, "art kit.description");
  requireStringArray(manifest.compatibleWorldTemplateIds, "art kit.compatibleWorldTemplateIds");
  requireStringArray(manifest.requiredAgentAnimations, "art kit.requiredAgentAnimations");

  const density = requireRecord(manifest.pixelDensity, "art kit.pixelDensity");
  invariant(density.scalePolicy === "integer-only", "pixel art must use integer-only scaling");
  requireRecord(density.logicalTileSize, "art kit.pixelDensity.logicalTileSize");
  requireRecord(density.characterFrame, "art kit.pixelDensity.characterFrame");

  const assets = requireArray(manifest.assetSlots, "art kit.assetSlots");
  invariant(assets.length >= 5, "art kit must declare terrain, buildings, interiors, props and avatars");
  const assetIds = new Set<string>();
  assets.forEach((asset, index) => {
    const record = requireRecord(asset, `assetSlots[${index}]`);
    const id = requireString(record.id, `assetSlots[${index}].id`);
    invariant(!assetIds.has(id), `duplicate asset slot ${id}`);
    assetIds.add(id);
    invariant(
      ["planned", "prototype", "ready"].includes(String(record.status)),
      `assetSlots[${index}] has invalid status`,
    );
    invariant(
      ["first_party", "generated_first_party", "planned_first_party"].includes(String(record.provenance)),
      `assetSlots[${index}] has forbidden provenance`,
    );
    invariant(
      record.license === "PROJECT_OWNED" || record.license === "PROJECT_GENERATED",
      `assetSlots[${index}] has forbidden license`,
    );
    if (record.sourcePath !== undefined) {
      const sourcePath = requireString(record.sourcePath, `assetSlots[${index}].sourcePath`);
      invariant(!/^https?:\/\//i.test(sourcePath), `assetSlots[${index}] must not point at remote commercial assets`);
    }
    if (record.status === "ready") {
      invariant(record.provenance !== "planned_first_party", `assetSlots[${index}] ready asset cannot have planned provenance`);
      requireString(record.sourcePath, `assetSlots[${index}].sourcePath`);
    }
  });

  return manifest as unknown as ArtKitManifest;
}

export function validateSpatialWorldManifest(value: unknown): SpatialWorldManifest {
  const manifest = requireRecord(value, "spatial world");
  invariant(manifest.schemaVersion === "spatial-world/v0", "unsupported spatial-world schemaVersion");
  requireString(manifest.id, "spatial world.id");
  requireString(manifest.version, "spatial world.version");
  validateLocalizedText(manifest.label, "spatial world.label");
  requireString(manifest.templateId, "spatial world.templateId");
  requireString(manifest.artKitId, "spatial world.artKitId");
  const rootNodeId = requireString(manifest.rootNodeId, "spatial world.rootNodeId");

  const nodes = requireArray(manifest.nodes, "spatial world.nodes").map(validateNode);
  const nodeById = new Map<string, SemanticWorldNode>();
  nodes.forEach((node) => {
    invariant(!nodeById.has(node.id), `duplicate node ${node.id}`);
    nodeById.set(node.id, node);
  });
  invariant(nodeById.has(rootNodeId), "root node does not exist");
  invariant(nodeById.get(rootNodeId)?.kind === "world", "root node must be a world");

  nodes.forEach((node) => {
    if (node.parentId) {
      const parent = nodeById.get(node.parentId);
      invariant(parent, `node ${node.id} references missing parent ${node.parentId}`);
      invariant(parent.childIds.includes(node.id), `parent ${parent.id} must list child ${node.id}`);
      invariant(node.zoomLevel >= parent.zoomLevel, `node ${node.id} cannot be shallower than its parent`);
    }
    node.childIds.forEach((childId) => {
      const child = nodeById.get(childId);
      invariant(child, `node ${node.id} references missing child ${childId}`);
      invariant(child.parentId === node.id, `child ${childId} must reference parent ${node.id}`);
    });
  });

  const portals = requireArray(manifest.portals, "spatial world.portals").map(validatePortal);
  const portalIds = new Set<string>();
  portals.forEach((portal) => {
    invariant(!portalIds.has(portal.id), `duplicate portal ${portal.id}`);
    portalIds.add(portal.id);
    const node = nodeById.get(portal.nodeId);
    invariant(node?.kind === "portal", `portal ${portal.id} must bind to a portal node`);
  });
  nodes.forEach((node) => {
    if (node.primaryPortalId) invariant(portalIds.has(node.primaryPortalId), `node ${node.id} has missing primary portal`);
  });

  return manifest as unknown as SpatialWorldManifest;
}

export function validateSpatialCatalog(
  templateValue: unknown,
  artKitValue: unknown,
  worldValue: unknown,
) {
  const template = validateWorldTemplateManifest(templateValue);
  const artKit = validateArtKitManifest(artKitValue);
  const world = validateSpatialWorldManifest(worldValue);
  invariant(world.templateId === template.id, "world template id does not match loaded template");
  invariant(world.artKitId === artKit.id, "world art-kit id does not match loaded art kit");
  invariant(template.supportedArtKitIds.includes(artKit.id), "template does not support loaded art kit");
  invariant(artKit.compatibleWorldTemplateIds.includes(template.id), "art kit does not support loaded template");
  invariant(template.defaultWorldId === world.id, "template default world does not match loaded world");
  return { template, artKit, world };
}
