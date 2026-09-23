// The flattened row list the Joints tree navigates by, and the batching behind "generate detail
// model". Both are pure, and both decide what a key press or a button click actually does.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { parseClashResult, detailBatches, specForJoint } = await import("@/state/clashCheckStore");
const { visibleRows, cursorIndex, step, groupRowFor } = await import("@/components/info_box_scene/joints/rows");

function fixtureDoc(): Record<string, unknown> {
  const joint = (id: string, typeKey: string, applicable: unknown[] = []) => ({
    id,
    centre: [0, 0, 0],
    members: [{ name: `bm-${id}`, kind: "BEAM", section: "I", member_type: "GIRDER" }],
    type_key: typeKey,
    type_label: typeKey.toUpperCase(),
    applicable,
  });
  return {
    schema: "ada.clash/result@1",
    source_key: "models/plant-a.ifc",
    options: {},
    counts: { members: 4, joints: 4 },
    joints: [
      joint("j1", "a", [{ spec: "builtin.girder_gusset", priority: 10 }]),
      joint("j2", "a", [
        { spec: "builtin.girder_gusset", priority: 10 },
        { spec: "external.fancy", capability: "fab", priority: 50 },
      ]),
      joint("j3", "b", []),
      joint("j4", "b", [{ spec: "external.fancy", capability: "fab", priority: 50 }]),
    ],
    groups: [
      { type_key: "a", type_label: "A", count: 2, joint_ids: ["j1", "j2"], applicable: [] },
      { type_key: "b", type_label: "B", count: 2, joint_ids: ["j3", "j4"], applicable: [] },
    ],
    provenance: {},
    warnings: [],
  };
}

test("collapsed: one row per group, no joints", () => {
  const result = parseClashResult(fixtureDoc());
  assert.deepEqual(
    visibleRows(result, null).map((r) => r.id),
    ["g:a", "g:b"],
  );
});

test("the open group's joints are inlined under it, and only that group's", () => {
  const result = parseClashResult(fixtureDoc());
  assert.deepEqual(
    visibleRows(result, "a").map((r) => r.id),
    ["g:a", "j:j1", "j:j2", "g:b"],
  );
});

test("the cursor is where it was last MOVED, not wherever the open group is", () => {
  // Up/Down must be able to pass a collapsed group without expanding it, so the cursor cannot be
  // a synonym for "the open group": it is its own state, and this is what resolves it.
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, "a");
  assert.equal(cursorIndex(rows, "g:b", "a", null), 3, "the cursor sits on a group that is closed");
  assert.equal(cursorIndex(rows, "j:j2", "a", null), 2);
});

test("with no cursor set, the focused joint then the open group stand in", () => {
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, "a");
  assert.equal(cursorIndex(rows, null, "a", "j2"), 2);
  assert.equal(cursorIndex(rows, null, "a", null), 0);
  assert.equal(cursorIndex(visibleRows(result, null), null, null, null), -1);
});

test("a cursor whose row is no longer on screen lands on the open group, not nowhere", () => {
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, "b"); // group a is closed, so j2 has no row of its own
  assert.equal(cursorIndex(rows, "j:j2", "b", null), 1, "g:b -- the group that IS open");
});

test("a focused joint in a group that is not open falls back to the group row", () => {
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, "a");
  // j4 belongs to group b, which has no rows on screen -- the cursor must not vanish.
  assert.equal(cursorIndex(rows, null, "a", "j4"), 0);
});

test("Up/Down clamp at the ends rather than wrapping round the model", () => {
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, "a");
  assert.equal(step(rows, 0, -1)?.id, "g:a");
  assert.equal(step(rows, rows.length - 1, 1)?.id, "g:b");
  assert.equal(step(rows, 1, 1)?.id, "j:j2");
});

test("Left from a joint goes up to its own group's row", () => {
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, "a");
  const joint = rows.find((r) => r.id === "j:j2")!;
  assert.equal(groupRowFor(rows, joint)?.id, "g:a");
  const group = rows.find((r) => r.id === "g:b")!;
  assert.equal(groupRowFor(rows, group)?.id, "g:b", "a group is its own parent row");
});

test("Down from no cursor starts at the top, Up from no cursor at the bottom", () => {
  const result = parseClashResult(fixtureDoc());
  const rows = visibleRows(result, null);
  assert.equal(step(rows, -1, 1)?.id, "g:a");
  assert.equal(step(rows, -1, -1)?.id, "g:b");
});

test("a joint is detailed by its highest-priority spec, once", () => {
  const result = parseClashResult(fixtureDoc());
  assert.equal(specForJoint(result.joints[1])?.spec, "external.fancy");
  const batches = detailBatches(result);
  const assigned = batches.flatMap((b) => b.jointIds);
  assert.equal(new Set(assigned).size, assigned.length, "no joint appears in two batches");
  assert.deepEqual(
    batches.map((b) => [b.spec.spec, [...b.jointIds]]),
    [
      ["external.fancy", ["j2", "j4"]],
      ["builtin.girder_gusset", ["j1"]],
    ],
  );
});

test("a joint no spec binds is left out entirely", () => {
  const result = parseClashResult(fixtureDoc());
  assert.ok(!detailBatches(result).flatMap((b) => b.jointIds).includes("j3"));
});

test("batching can be restricted to the joints on screen", () => {
  const result = parseClashResult(fixtureDoc());
  assert.deepEqual(
    detailBatches(result, ["j1"]).map((b) => [b.spec.spec, [...b.jointIds]]),
    [["builtin.girder_gusset", ["j1"]]],
  );
  assert.deepEqual(detailBatches(result, []), []);
});

// --- the PRODUCED take-off a detail run writes beside its GLB ---------------------------------

const { parseDetailStats, mergeProducedJoints } = await import("@/state/clashCheckStore");

function takeoff(names: readonly string[], slug = "gusset") {
  return {
    joints: {
      count: names.length,
      by_type: [{ slug, name: slug, count: names.length }],
      items: names.map((name) => ({
        name,
        slug,
        type: slug,
        members: ["g1", "g2"],
        plates: 1,
        welds: 2,
        centre: [0, 0, 3],
      })),
    },
  };
}

test("a run's take-off is read field by field", () => {
  const stats = parseDetailStats(takeoff(["a", "b"]));
  assert.equal(stats.joints?.count, 2);
  assert.deepEqual(stats.joints?.by_type.map((t) => t.slug), ["gusset"]);
  assert.equal(stats.joints?.items[0].welds, 2);
  assert.deepEqual(stats.skipped, []);
});

test("skipped joints are carried, not dropped", () => {
  const stats = parseDetailStats({ ...takeoff(["a"]), skipped: ["j9: refused"] });
  assert.deepEqual(stats.skipped, ["j9: refused"]);
});

test("a document that is not a take-off yields an empty one rather than throwing", () => {
  assert.deepEqual(parseDetailStats(null), { joints: null, skipped: [] });
  assert.deepEqual(parseDetailStats({ joints: "nope" }), { joints: null, skipped: [] });
});

test("one job per spec adds up to one table", () => {
  const a = parseDetailStats(takeoff(["a1", "a2"], "gusset")).joints;
  const b = parseDetailStats(takeoff(["b1"], "box")).joints;
  const merged = mergeProducedJoints(a, b);
  assert.equal(merged?.count, 3);
  assert.deepEqual(
    merged?.by_type.map((t) => [t.slug, t.count]),
    [
      ["gusset", 2],
      ["box", 1],
    ],
  );
});

test("re-running the same spec does not double the count", () => {
  const first = parseDetailStats(takeoff(["a1", "a2"])).joints;
  const again = parseDetailStats(takeoff(["a1", "a2"])).joints;
  const merged = mergeProducedJoints(first, again);
  assert.equal(merged?.count, 2, "the same joints rebuilt are the same joints");
  assert.deepEqual(merged?.by_type.map((t) => t.count), [2]);
});

test("merging with nothing on either side is the other side", () => {
  const only = parseDetailStats(takeoff(["a"])).joints;
  assert.equal(mergeProducedJoints(null, only), only);
  assert.equal(mergeProducedJoints(only, null), only);
  assert.equal(mergeProducedJoints(null, null), null);
});
