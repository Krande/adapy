// Filtering a result by WHICH PASS produced its joints.
//
// Two passes over one model find overlapping but different joints -- a shared node and a
// two-millimetre overlap are both true -- so a reader has to be able to tell them apart and turn
// one off. What matters here is that the filter is ONE derivation: the rows, the 3D markers and
// the counts all read it, and a filter applied to some of them and not others is how a panel ends
// up listing eight joints under a heading that says twelve.

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

const { originsInResult, parseClashResult, visibleResult, withoutHiddenOrigins } = await import(
  "@/state/clashCheckStore"
);

function joint(id: string, origin: string, typeKey: string, contact?: Record<string, unknown>) {
  return {
    id,
    centre: [0, 0, 0],
    members: [
      { name: `${id}_a`, kind: "BEAM" },
      { name: `${id}_b`, kind: "BEAM" },
    ],
    type_key: typeKey,
    type_label: typeKey,
    applicable: [],
    origin,
    ...(contact ? { contact } : {}),
  };
}

function doc() {
  return {
    schema: "ada.clash/result@1",
    source_key: "m.ifc",
    options: {},
    counts: { members: 9, joints: 3 },
    joints: [
      joint("j1", "beam-beam", "k1"),
      joint("j2", "beam-beam", "k1"),
      joint("j3", "mesh", "k2", { penetration_depth: 0.002 }),
    ],
    groups: [
      { type_key: "k1", type_label: "k1", count: 2, joint_ids: ["j1", "j2"], applicable: [] },
      { type_key: "k2", type_label: "k2", count: 1, joint_ids: ["j3"], applicable: [] },
    ],
    provenance: {},
    warnings: [],
    passes: [
      { name: "beam-beam", ran: true, found: 2 },
      { name: "mesh", ran: true, found: 1, capability: "detail-clash" },
      { name: "plate-beam", ran: false, reason: "not selected" },
    ],
  };
}

test("every joint reports which pass produced it", () => {
  const r = parseClashResult(doc());
  assert.deepEqual(
    r.joints.map((j) => j.origin),
    ["beam-beam", "beam-beam", "mesh"],
  );
});

test("a geometric pass's measurement survives parsing, an axis pass's absence stays absent", () => {
  // The data a generator sizes its output from. Carried, never interpreted here.
  const r = parseClashResult(doc());
  assert.equal(r.joints[0].contact, null);
  assert.deepEqual(r.joints[2].contact, { penetration_depth: 0.002 });
});

test("a document written before joints carried a producer still reads", () => {
  // Defaulted rather than required: back then core's beam pass was the only one there, so that
  // is what those joints are, and refusing the document would lose a cached result for nothing.
  const raw = doc();
  const legacy = { ...raw, joints: raw.joints.map(({ origin, ...rest }) => rest) };
  const r = parseClashResult(legacy);
  assert.deepEqual(new Set(r.joints.map((j) => j.origin)), new Set(["beam-beam"]));
});

test("the filter offers exactly the producers the result contains, with counts", () => {
  const r = parseClashResult(doc());
  assert.deepEqual(originsInResult(r), [
    { origin: "beam-beam", count: 2 },
    { origin: "mesh", count: 1 },
  ]);
});

test("hiding a producer removes its joints, its groups and its count together", () => {
  const r = parseClashResult(doc());
  const filtered = withoutHiddenOrigins(r, ["mesh"]);

  assert.deepEqual(
    filtered.joints.map((j) => j.id),
    ["j1", "j2"],
  );
  // A group left with no visible joints is dropped, not left empty: an empty row invites a click
  // that can select nothing.
  assert.deepEqual(
    filtered.groups.map((g) => g.typeKey),
    ["k1"],
  );
  // The count a reader compares against the rows has to move with them.
  assert.equal(filtered.counts.joints, 2);
  assert.equal(filtered.jointsById.has("j3"), false);
});

test("a count about the SOURCE is left alone", () => {
  // `members` is a fact about the model, not about which passes are being shown. Filtering it
  // would make the panel claim the file has fewer members than it does.
  const filtered = withoutHiddenOrigins(parseClashResult(doc()), ["mesh"]);
  assert.equal(filtered.counts.members, 9);
});

test("hiding nothing returns the same object", () => {
  // The common case must allocate nothing, so a useMemo on it stays stable and the 3D overlay is
  // not rebuilt on every unrelated store change.
  const r = parseClashResult(doc());
  assert.equal(withoutHiddenOrigins(r, []), r);
  assert.equal(visibleResult({ result: r, hiddenOrigins: [] }), r);
});

test("hiding every producer yields an empty result rather than a broken one", () => {
  const filtered = withoutHiddenOrigins(parseClashResult(doc()), ["beam-beam", "mesh"]);
  assert.deepEqual(filtered.joints, []);
  assert.deepEqual(filtered.groups, []);
  assert.equal(filtered.counts.joints, 0);
});

test("the passes report carries what did not run, and why", () => {
  // "Not run" and "found nothing" are different answers; the panel offers the difference as a
  // checkbox, and a capability says which pool a pass needs.
  const r = parseClashResult(doc());
  const byName = new Map(r.passes.map((p) => [p.name, p]));
  assert.equal(byName.get("beam-beam")?.found, 2);
  assert.equal(byName.get("plate-beam")?.ran, false);
  assert.equal(byName.get("plate-beam")?.reason, "not selected");
  assert.equal(byName.get("mesh")?.capability, "detail-clash");
});
