// Built-in, client-side procedural start-from templates for the storage
// "New model from template" dropdown. These run on the adapy-default engine
// (the in-repo compiler, runnable server-side and in the browser via WASM) so
// they always appear regardless of which workers are up. Worker-backed engines
// (e.g. pm-engine) advertise THEIR templates through
// GET /procedural-templates, which the dropdown appends to these.
//
// Each builder returns a FRESH document every call — the docs are committed
// verbatim into a new model, so callers must never share a mutable instance.
import type { ProceduralDoc, ProceduralTemplate } from "../services/viewerApi";

/** The reference steel-structure demo: a two-storey framed structure with a
 * fully-enclosed HVAC room, routed process/electrical/duct services and
 * two-ended site I/O. Also drives the cellbuilder's "Load demo" button. */
export function steelStructureDemoDoc(): ProceduralDoc {
  return {
    grid: {},
    // Only Cell3 (the HVAC room) is fully enclosed — plated walls + decks;
    // the other cells stay open steel frame.
    blueprint: { enclosed_cells: ["Cell3"] },
    design_rules: "standard",
    spaces: [
      { NAME: "Cell1", INCLUDE: true, X: 0, Y: 0, Z: 0, DX: 5, DY: 5, DZ: 3 },
      { NAME: "Cell2", INCLUDE: true, X: 5, Y: 0, Z: 0, DX: 5, DY: 5, DZ: 3 },
      { NAME: "Cell3", INCLUDE: true, X: 0, Y: 0, Z: 3, DX: 5, DY: 5, DZ: 3 },
      { NAME: "Cell4", INCLUDE: true, X: 5, Y: 0, Z: 3, DX: 5, DY: 5, DZ: 3 },
    ],
    equipments: [
      // Ground floor (Cell1/Cell2)
      { NAME: "Pump2", DESCRIPTION: "pump", X: 2, Y: 2, Z: 0, LX: 1, LY: 1, LZ: 1 },
      { NAME: "Tank2", DESCRIPTION: "tank", X: 6.5, Y: 1.5, Z: 0, LX: 2, LY: 2, LZ: 2 },
      { NAME: "SB2", DESCRIPTION: "switchboard", X: 0.3, Y: 2, Z: 0, LX: 0.8, LY: 0.4, LZ: 1.2 },
      // Second floor (Cell3/Cell4) — Cell3 is the HVAC room
      { NAME: "Pump1", DESCRIPTION: "pump", X: 2, Y: 2, Z: 3, LX: 1, LY: 1, LZ: 1 },
      { NAME: "Tank1", DESCRIPTION: "tank", X: 6.5, Y: 1.5, Z: 3, LX: 2, LY: 2, LZ: 2 },
      { NAME: "SB1", DESCRIPTION: "switchboard", X: 0.3, Y: 2, Z: 3, LX: 0.8, LY: 0.4, LZ: 1.2 },
      { NAME: "HVAC1", DESCRIPTION: "hvac", X: 3, Y: 3.5, Z: 3, LX: 1.5, LY: 1, LZ: 1.2 },
      // Roof — the duct exhausts up to this unit on top of the structure
      { NAME: "Exhaust1", DESCRIPTION: "exhaust_fan", X: 3, Y: 3.5, Z: 6, LX: 0.8, LY: 0.8, LZ: 0.6 },
    ],
    systems: [
      // Process piping
      {
        NAME: "CoolingWater",
        TYPE: "piping",
        MEDIUM: "water",
        CONNECTIONS: [
          { EQUIPMENT: "Pump1", PORT: "discharge" },
          { EQUIPMENT: "Tank1", PORT: "inlet" },
        ],
      },
      {
        NAME: "ServiceWater",
        TYPE: "piping",
        MEDIUM: "water",
        CONNECTIONS: [
          { EQUIPMENT: "Pump2", PORT: "discharge" },
          { EQUIPMENT: "Tank2", PORT: "inlet" },
        ],
      },
      // Electrical distribution: mains enters at the Cell1 edge into the ground
      // switchboard (SB2), which feeds the local pump AND a second switchboard
      // (SB1) up in Cell3; SB1 in turn feeds its room's loads.
      {
        NAME: "Mains",
        TYPE: "electrical",
        CONNECTIONS: [
          { SITE: "grid_supply", POSITION: [0, 1, 1], DIRECTION: "IN", DIRECTION_VECTOR: [1, 0, 0] },
          { EQUIPMENT: "SB2", PORT: "incoming" },
        ],
      },
      {
        NAME: "PowerFeed2",
        TYPE: "electrical",
        CONNECTIONS: [
          { EQUIPMENT: "SB2", PORT: "feeder" },
          { EQUIPMENT: "Pump2", PORT: "power" },
        ],
      },
      {
        NAME: "DeckTie",
        TYPE: "electrical",
        CONNECTIONS: [
          { EQUIPMENT: "SB2", PORT: "feeder2" },
          { EQUIPMENT: "SB1", PORT: "incoming" },
        ],
      },
      {
        NAME: "PowerFeed1",
        TYPE: "electrical",
        CONNECTIONS: [
          { EQUIPMENT: "SB1", PORT: "feeder" },
          { EQUIPMENT: "Pump1", PORT: "power" },
        ],
      },
      {
        NAME: "HvacPower",
        TYPE: "electrical",
        CONNECTIONS: [
          { EQUIPMENT: "SB1", PORT: "feeder2" },
          { EQUIPMENT: "HVAC1", PORT: "power" },
        ],
      },
      // HVAC duct: the room's air handler exhausts up to the roof fan
      {
        NAME: "HvacExhaust",
        TYPE: "duct",
        MEDIUM: "air",
        CONNECTIONS: [
          { EQUIPMENT: "HVAC1", PORT: "supply" },
          { EQUIPMENT: "Exhaust1", PORT: "intake" },
        ],
      },
      // Remaining site I/O — all at the Cell1 edge (x=0)
      {
        NAME: "Drain",
        TYPE: "piping",
        MEDIUM: "water",
        CONNECTIONS: [
          { EQUIPMENT: "Tank2", PORT: "outlet" },
          { SITE: "drain", POSITION: [0, 2.5, 1], DIRECTION: "OUT", DIRECTION_VECTOR: [1, 0, 0] },
        ],
      },
      {
        NAME: "Suction",
        TYPE: "piping",
        MEDIUM: "water",
        CONNECTIONS: [
          { SITE: "seawater", POSITION: [0, 4, 1], DIRECTION: "IN", DIRECTION_VECTOR: [1, 0, 0] },
          { EQUIPMENT: "Pump2", PORT: "suction" },
        ],
      },
    ],
    openings: [],
  };
}

/** A combined topside-on-jacket: a framed steel topside deck (SteelStru box
 * cells + equipment + a cooling-water run) sitting atop an open tubular jacket
 * truss (a REPRESENTATION="JACKET" loft member tapering seabed→deck). Both are
 * built in one ProceduralBuilder pass — the structural blueprint frames the
 * deck cells while the loft path emits the jacket legs/ring/braces. */
export function topsideJacketDoc(): ProceduralDoc {
  return {
    grid: {},
    blueprint: {},
    design_rules: "standard",
    // Topside: two deck cells over a 24×24 footprint (X/Y −12..12), matching the
    // jacket's top, split into a two-storey deck (z 100..108).
    spaces: [
      { NAME: "DeckA", INCLUDE: true, X: -12, Y: -12, Z: 100, DX: 12, DY: 24, DZ: 4 },
      { NAME: "DeckB", INCLUDE: true, X: 0, Y: -12, Z: 100, DX: 12, DY: 24, DZ: 4 },
      { NAME: "DeckA2", INCLUDE: true, X: -12, Y: -12, Z: 104, DX: 12, DY: 24, DZ: 4 },
      { NAME: "DeckB2", INCLUDE: true, X: 0, Y: -12, Z: 104, DX: 12, DY: 24, DZ: 4 },
    ],
    equipments: [
      { NAME: "Pump", DESCRIPTION: "pump", X: -6, Y: -1, Z: 100, LX: 1, LY: 1, LZ: 1 },
      { NAME: "Tank", DESCRIPTION: "tank", X: 3, Y: -1, Z: 100, LX: 2, LY: 2, LZ: 2 },
    ],
    systems: [
      {
        NAME: "CoolingWater",
        TYPE: "piping",
        MEDIUM: "water",
        CONNECTIONS: [
          { EQUIPMENT: "Pump", PORT: "discharge" },
          { EQUIPMENT: "Tank", PORT: "inlet" },
        ],
      },
    ],
    openings: [],
    // Jacket substructure: a rectangular loft tapering from a 40×40 seabed base
    // to the 24×24 deck footprint, rendered as an open tubular truss.
    loft_members: [
      {
        NAME: "Jacket",
        INCLUDE: true,
        REPRESENTATION: "JACKET",
        STATIONS: [
          { TYPE: "rectangle", X: 0, Y: 0, Z: 0, WIDTH: 40, HEIGHT: 40, SEGMENTS: 4 },
          { TYPE: "rectangle", X: 0, Y: 0, Z: 20, WIDTH: 40, HEIGHT: 40, SEGMENTS: 4 },
          { TYPE: "rectangle", X: 0, Y: 0, Z: 60, WIDTH: 31, HEIGHT: 31, SEGMENTS: 4 },
          { TYPE: "rectangle", X: 0, Y: 0, Z: 100, WIDTH: 24, HEIGHT: 24, SEGMENTS: 4 },
        ],
      },
    ],
  };
}

/** Client-side built-in templates, prepended to the server-advertised
 * (worker-backed) ones. Each carries an inline doc committed verbatim. */
export const BUILTIN_PROCEDURAL_TEMPLATES: ProceduralTemplate[] = [
  {
    id: "builtin:steel-demo",
    name: "Steel structure demo",
    engine: "adapy-default",
    doc: steelStructureDemoDoc(),
  },
  {
    id: "builtin:topside-jacket",
    name: "Topside + jacket",
    engine: "adapy-default",
    doc: topsideJacketDoc(),
  },
];
