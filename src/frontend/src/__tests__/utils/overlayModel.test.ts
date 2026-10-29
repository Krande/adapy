import assert from "node:assert/strict";
import { test } from "node:test";

import type { NodeRelation } from "@/services/api/nodeRelations";
import type { Aabb } from "@/utils/bboxSelection/boxMath";
import { markCount, OVERLAY_RELATIONS, overlayMarks } from "@/utils/overlays/overlayModel";

const box = (x: number): Aabb => ({ min: [x, 0, 0], max: [x + 1, 1, 1] });
const drawn: Record<string, Aabb> = { A: box(0), B: box(2), C: box(4), PIECE: box(10), PLATE: box(12) };
const boundsOf = (n: string) => drawn[n] ?? null;

const rel = (from: string, relation: string, to: string, props: Record<string, unknown> = {}): NodeRelation => ({
  provider: "p",
  from_node_id: from.toLowerCase(),
  from_full_name: from,
  relation,
  to_node_id: null,
  to_full_name: to,
  props,
});

test("every overlay reads only core relation words", () => {
  assert.deepEqual(
    [...OVERLAY_RELATIONS].sort(),
    ["connects_to", "fills_opening", "flows_to", "penetrates", "serves_system"],
  );
});

test("systems colour their drawn members once, largest first", () => {
  const m = overlayMarks(
    [
      rel("A", "serves_system", "/S1", { system_kind: "duct" }),
      rel("B", "serves_system", "/S1"),
      rel("B", "serves_system", "/S1"),
      rel("C", "serves_system", "/S2"),
      rel("NOT-DRAWN", "serves_system", "/S3"),
    ],
    boundsOf,
  );
  assert.deepEqual(
    m.systems.entries.map((e) => [e.name, e.kind, e.members]),
    [["/S1", "duct", ["A", "B"]], ["/S2", null, ["C"]]],
  );
  assert.equal(m.systems.boxes.length, 3);
  assert.notEqual(m.systems.entries[0].colour, m.systems.entries[1].colour);
});

test("flow arrows run centre to centre and say what their direction is based on", () => {
  const m = overlayMarks(
    [rel("A", "flows_to", "B", { basis: "flow" }), rel("B", "flows_to", "C"), rel("C", "flows_to", "GONE")],
    boundsOf,
  );
  assert.deepEqual(m.flow.arrows, [
    { from: [0.5, 0.5, 0.5], to: [2.5, 0.5, 0.5], basis: "flow" },
    { from: [2.5, 0.5, 0.5], to: [4.5, 0.5, 0.5], basis: "modelled" },
  ]);
  assert.deepEqual([m.flow.flowKnown, m.flow.modelledOnly], [1, 1]);
  assert.equal(markCount(m, "flow"), 2);
});

test("a penetration piece is marked once and linked to its host when the host is drawn", () => {
  const m = overlayMarks(
    [
      rel("PIECE", "fills_opening", "/OPENING", { host_full_name: "PLATE" }),
      rel("PIECE", "penetrates", "PLATE"),
      rel("PIECE", "connects_to", "A"),
    ],
    boundsOf,
  );
  assert.equal(m.penetrations.boxes.length, 1);
  assert.equal(m.penetrations.links.length, 2);
  assert.deepEqual(m.connections.links, [{ from: [10.5, 0.5, 0.5], to: [0.5, 0.5, 0.5] }]);
});
