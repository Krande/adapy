import assert from "node:assert/strict";
import { test } from "node:test";

import type {
  ProceduralTypeOption,
  TypePortSummary,
} from "../../services/viewerApi";
import {
  effectivePorts,
  portsForEquipment,
  portSnapTargets,
  readPortOverrides,
  typePickerItems,
  withPortOverride,
} from "../../utils/cellbuilder/ports";

const port = (over: Partial<TypePortSummary> = {}): TypePortSummary => ({
  name: "suction",
  direction: "IN",
  category: "process",
  position: [0.5, 0, 1],
  direction_vector: [1, 0, 0],
  ...over,
});

test("readPortOverrides parses well-formed entries and drops junk", () => {
  const params = {
    PORT_OVERRIDES: {
      suction: { position: [1, 2, 3], direction_vector: [0, 0, 1] },
      discharge: { position: [4, 5, 6] },
      bad1: { position: [1, 2] }, // wrong length → dropped field
      bad2: "nope", // not an object → skipped
      empty: {}, // no editable fields → skipped
    },
  };
  const o = readPortOverrides(params);
  assert.deepEqual(o.suction, {
    position: [1, 2, 3],
    direction_vector: [0, 0, 1],
  });
  assert.deepEqual(o.discharge, { position: [4, 5, 6] });
  assert.equal("bad1" in o, false);
  assert.equal("bad2" in o, false);
  assert.equal("empty" in o, false);
});

test("readPortOverrides tolerates missing / malformed params", () => {
  assert.deepEqual(readPortOverrides(undefined), {});
  assert.deepEqual(readPortOverrides(null), {});
  assert.deepEqual(readPortOverrides({}), {});
  assert.deepEqual(readPortOverrides({ PORT_OVERRIDES: 7 }), {});
});

test("withPortOverride merges a patch immutably, preserving other fields", () => {
  const start = { suction: { position: [0, 0, 0] as [number, number, number] } };
  const next = withPortOverride(start, "suction", { direction_vector: [0, 1, 0] });
  // original untouched
  assert.deepEqual(start.suction, { position: [0, 0, 0] });
  // merged, keeping the prior position
  assert.deepEqual(next.suction, {
    position: [0, 0, 0],
    direction_vector: [0, 1, 0],
  });
});

test("withPortOverride adds a new port entry", () => {
  const next = withPortOverride({}, "power", { position: [1, 1, 1] });
  assert.deepEqual(next, { power: { position: [1, 1, 1] } });
});

test("effectivePorts overlays position/direction by name, leaving others", () => {
  const ports = [port({ name: "suction" }), port({ name: "discharge" })];
  const merged = effectivePorts(ports, {
    suction: { position: [9, 9, 9], direction_vector: [0, 0, -1] },
  });
  assert.deepEqual(merged[0].position, [9, 9, 9]);
  assert.deepEqual(merged[0].direction_vector, [0, 0, -1]);
  // discharge untouched (same reference is fine, values identical)
  assert.deepEqual(merged[1].position, [0.5, 0, 1]);
  // category/direction preserved on the overridden port
  assert.equal(merged[0].direction, "IN");
  assert.equal(merged[0].category, "process");
});

test("effectivePorts keeps a type field when the override omits it", () => {
  const ports = [port({ name: "suction", position: [2, 2, 2] })];
  const merged = effectivePorts(ports, {
    suction: { direction_vector: [0, 1, 0] },
  });
  assert.deepEqual(merged[0].position, [2, 2, 2]); // unchanged
  assert.deepEqual(merged[0].direction_vector, [0, 1, 0]); // overridden
});

test("effectivePorts is a no-op on an empty port list", () => {
  const empty: TypePortSummary[] = [];
  assert.equal(effectivePorts(empty, { x: { position: [1, 1, 1] } }), empty);
});

test("portSnapTargets returns the 8 bbox corners (model space)", () => {
  const targets = portSnapTargets({ origin: [0, 0, 0], size: [2, 2, 2] });
  assert.equal(targets.length, 8);
  // corners span the box extremes
  assert.ok(targets.some((t) => t[0] === 0 && t[1] === 0 && t[2] === 0));
  assert.ok(targets.some((t) => t[0] === 2 && t[1] === 2 && t[2] === 2));
});

test("portSnapTargets appends CAD vertices when supplied", () => {
  const cad: [number, number, number][] = [
    [0.1, 0.2, 0.3],
    [1.1, 1.2, 1.3],
  ];
  const targets = portSnapTargets({ origin: [0, 0, 0], size: [1, 1, 1] }, cad);
  assert.equal(targets.length, 10);
  assert.ok(targets.some((t) => t[0] === 0.1 && t[1] === 0.2 && t[2] === 0.3));
});

test("typePickerItems derives labels with the origin tag", () => {
  const items = typePickerItems([
    { slug: "pump", name: "Pump", origin: "code" },
    { slug: "tank42", name: "Tank 42", origin: "catalog" },
  ]);
  assert.deepEqual(items, [
    { key: "pump", label: "Pump (code)", slug: "pump", origin: "code" },
    { key: "tank42", label: "Tank 42 (db)", slug: "tank42", origin: "catalog" },
  ]);
});

test("typePickerItems maps an empty list to empty", () => {
  assert.deepEqual(typePickerItems([]), []);
});

const pumpType: ProceduralTypeOption = {
  slug: "pump",
  name: "Centrifugal Pump",
  origin: "code",
  ports: [
    port({ name: "suction", position: [0, 0, 0] }),
    port({ name: "discharge", position: [1, 0, 0], direction: "OUT" }),
  ],
};

test("portsForEquipment returns the archetype ports for a matched slug", () => {
  const cell = { kind: "equipment", equipmentType: "pump", params: {} };
  const ports = portsForEquipment(cell, [pumpType]);
  assert.deepEqual(
    ports.map((p) => p.name),
    ["suction", "discharge"],
  );
  // no overrides on the cell → geometry passes through verbatim
  assert.deepEqual(ports[0].position, [0, 0, 0]);
});

test("portsForEquipment matches by name (case-insensitive) when no slug hits", () => {
  const cell = {
    kind: "equipment",
    equipmentType: "CENTRIFUGAL pump",
    params: {},
  };
  const ports = portsForEquipment(cell, [pumpType]);
  assert.equal(ports.length, 2);
});

test("portsForEquipment overlays this instance's PORT_OVERRIDES", () => {
  const cell = {
    kind: "equipment",
    equipmentType: "pump",
    params: {
      PORT_OVERRIDES: {
        suction: { position: [9, 9, 9], direction_vector: [0, 0, -1] },
      },
    },
  };
  const ports = portsForEquipment(cell, [pumpType]);
  const suction = ports.find((p) => p.name === "suction")!;
  assert.deepEqual(suction.position, [9, 9, 9]);
  assert.deepEqual(suction.direction_vector, [0, 0, -1]);
  // untouched port keeps its archetype geometry
  assert.deepEqual(ports.find((p) => p.name === "discharge")!.position, [1, 0, 0]);
});

test("portsForEquipment yields [] for a non-equipment cell", () => {
  const cell = { kind: "cell", equipmentType: "pump", params: {} };
  assert.deepEqual(portsForEquipment(cell, [pumpType]), []);
});

test("portsForEquipment yields [] when the type is missing or has no ports", () => {
  const noType = { kind: "equipment", equipmentType: "ghost", params: {} };
  assert.deepEqual(portsForEquipment(noType, [pumpType]), []);
  const portless: ProceduralTypeOption = {
    slug: "beam",
    name: "Beam",
    origin: "code",
  };
  const cell = { kind: "equipment", equipmentType: "beam", params: {} };
  assert.deepEqual(portsForEquipment(cell, [portless]), []);
});

test("portsForEquipment yields [] for equipment with no equipmentType", () => {
  const cell = { kind: "equipment", params: {} };
  assert.deepEqual(portsForEquipment(cell, [pumpType]), []);
});
