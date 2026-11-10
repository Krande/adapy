// The ids of core's own panels, apart from their catalogue (`./corePanels`,
// which imports the stores), so the dependency-free facade root can export them:
// a shell names core panels in its default layout without loading them.

/** The owner id core registers its panels under. */
export const CORE_PANEL_OWNER = "core";

/** Stable ids of core's panels. */
export const CORE_PANEL_IDS = {
  options: "core:options",
  tree: "core:tree",
  storage: "core:storage",
  serverInfo: "core:server-info",
  attributeSearch: "core:attribute-search",
  objectInfo: "core:object-info",
  scene: "core:scene",
  wsStatus: "core:ws-status",
  simulation: "core:simulation",
  feaTable: "core:fea-table",
  componentControls: "core:component-controls",
  cellbuilder: "core:cellbuilder",
  /** The procedural model's cells, lofts, openings and equipment, as a list of their own. */
  componentList: "core:component-list",
  equipmentAdmin: "core:equipment-admin",
  systemAdmin: "core:system-admin",
  admin: "core:admin",
  convert: "core:convert",
  /** Simulation providers and the scope's simulation runs. */
  simulations: "core:simulations",
} as const;

export type CorePanelId = (typeof CORE_PANEL_IDS)[keyof typeof CORE_PANEL_IDS];
