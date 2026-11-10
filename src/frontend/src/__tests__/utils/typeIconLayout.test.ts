import assert from "node:assert/strict";
import { test } from "node:test";

import type { BuilderCell, BuilderSystem } from "../../state/cellbuilder/types";
import { layoutTypeIcons, typeIconScale, type TypeIconInput } from "../../utils/viewer/typeIconLayout";

const cell = (name: string, origin: [number, number, number], extra: Partial<BuilderCell> = {}): BuilderCell =>
  ({ id: name, name, kind: "equipment", origin, size: [2, 2, 2], ...extra }) as BuilderCell;

const cells = {
  pump: cell("pump", [0, 0, 0], { equipmentType: "feed-pump" }),
  tank: cell("tank", [10, 0, 0], { equipmentType: "tank" }),
  room: cell("room", [0, 0, 0], { kind: "cell", size: [20, 4, 3] }),
};
const systems: Record<string, BuilderSystem> = {
  feed: {
    id: "feed",
    name: "Feed",
    type: "piping",
    medium: "sea water",
    connections: [
      { equipment: "pump", port: "discharge" },
      { equipment: "tank", port: "inlet" },
    ],
  } as BuilderSystem,
};
const equipmentTypes = [
  { slug: "feed-pump", name: "Feed pump", origin: "builtin", ports: [
    { name: "suction", direction: "IN", category: "process" },
    { name: "discharge", direction: "OUT", category: "process" },
  ] },
] as unknown as TypeIconInput["equipmentTypes"];

const input = (over: Partial<TypeIconInput> = {}): TypeIconInput => ({
  cells,
  systems,
  equipmentTypes,
  show: { equipment: true, media: true, missing: true },
  scale: 1,
  translation: null,
  routeCentre: () => null,
  ...over,
});

test("the icon size follows the model's box, or the cells when no model is loaded", () => {
  assert.equal(typeIconScale([0, 0, 0, 30, 40, 0], cells), 50 * 0.03);
  assert.equal(typeIconScale(null, cells), 20 * 0.6);
  assert.equal(typeIconScale(null, {}), 0.6);
});

test("equipment gets its archetype icon above its box, and a warning for unconnected inputs", () => {
  const icons = layoutTypeIcons(input({ show: { equipment: true, media: false, missing: true } }));
  assert.deepEqual(
    icons.map((i) => [i.kind, i.kind === "medium" ? i.system : i.equipment]),
    [["equipment", "pump"], ["missing", "pump"], ["equipment", "tank"]],
    "only equipment cells, and only the pump has an input left open",
  );
  const [pump, warn, tank] = icons;
  assert.deepEqual(pump, { kind: "equipment", icon: "pump", equipment: "pump", position: [1, 1, 2.75], size: 1 });
  assert.deepEqual(warn, { kind: "missing", equipment: "pump", missing: ["suction"], position: [1.55, 1, 3.6], size: 0.8 });
  assert.equal(tank.kind === "equipment" && tank.icon, "tank");
});

test("the model's translation moves cell icons into the world", () => {
  const [pump] = layoutTypeIcons(input({ translation: [100, 200, 300], show: { equipment: true, media: false, missing: false } }));
  assert.deepEqual(pump.position, [101, 201, 302.75]);
});

test("a medium marker sits over the routed pipe, or over the connected equipment before there is one", () => {
  const only = { equipment: false, media: true, missing: false };
  const [fallback] = layoutTypeIcons(input({ show: only, translation: [0, 0, 10] }));
  assert.deepEqual(fallback, { kind: "medium", icon: "water", system: "Feed", position: [6, 1, 2 + 0.75 * 1.6 + 10], size: 0.85 });

  // The pipe's centre is a world point already: the translation is not added again.
  const asked: string[] = [];
  const [routed] = layoutTypeIcons(input({ show: only, translation: [0, 0, 10], routeCentre: (s) => (asked.push(s), [5, 5, 5]) }));
  assert.deepEqual(asked, ["Feed"]);
  assert.deepEqual(routed.position, [5, 5, 5 + 0.75 * 0.4]);

  const unconnected = { lone: { ...systems.feed, name: "Lone", connections: [{ site: "shore" }] } as BuilderSystem };
  assert.deepEqual(layoutTypeIcons(input({ show: only, systems: unconnected })), []);
});

test("each kind of icon is drawn only when asked for", () => {
  assert.deepEqual(layoutTypeIcons(input({ show: { equipment: false, media: false, missing: false } })), []);
});
