// The steel-structure demo document used by the cellbuilder's "Load demo"
// button (see cellBuilderStore.loadDemoTemplate) to populate the currently-open
// model. The "New model from template" DROPDOWN no longer sources its templates
// from here — those are advertised by live workers and fetched from
// GET /procedural-templates (the base worker announces the same adapy-default
// demos, defined in ada.topo_model.templates). This client-side copy exists only
// so the Load-demo button can seed a model without a round-trip.
import type { ProceduralDoc } from "../services/viewerApi";

/** Returns a FRESH document every call — it is committed/applied into a model,
 * so callers must never share a mutable instance. */
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
