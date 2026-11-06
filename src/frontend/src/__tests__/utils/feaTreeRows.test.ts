// The FEA result's sets and properties as Scene tree rows: ids, rows, the union an
// isolation keeps, and the reasons its toggles may be unavailable.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { FeaManifestField } from "@/services/api/fea";
import {
  FEA_ELEMENT_SETS_ID,
  FEA_NODE_SETS_ID,
  FEA_PROPERTIES_ROOT_ID,
  FEA_SETS_ROOT_ID,
  buildFeaTreeRoots,
  buildPropertyRow,
  buildSetsRoot,
  combineIsolation,
  contextWireframeReason,
  elementSetFromRowId,
  elementSetRowId,
  isFeaTreeRowId,
  isIsolatableRowId,
  isolatedMembers,
  isolationReason,
  nodeSetRowId,
  propertyFieldsOf,
  propertyLabel,
  propertyValueFromRowId,
  propertyValueLabel,
  propertyValueRowId,
  setRowsMatching,
  setsByElement,
  type FeaSetInfo,
} from "@/utils/scene/fea/feaTreeRows";

const SETS: FeaSetInfo[] = [
  { name: "Deck", members: ["EL1", "EL2"], fe_object_type: "element" },
  { name: "Side", members: ["EL2", "EL3"], fe_object_type: "element" },
  { name: "Deck", members: ["P1", "P2", "P3"], fe_object_type: "node" },
  { name: "Supports", members: ["P9"], fe_object_type: "node" },
];

const field = (over: Partial<FeaManifestField>): FeaManifestField =>
  ({
    name_canonical: "props.material",
    name_native: "MAT",
    kind: "scalar",
    category: "property",
    support: "element_average",
    analysis_kind: "static",
    components: ["value"],
    per_type: [{ elem_type: "quad", n_elements: 3, n_ips: 1, ip_layout: [], blob: {} as never, scalar_range: {} as never }],
    n_steps: 1,
    steps: [],
    scalar_range: {} as never,
    default_view: { reduction: "scalar", colormap: "viridis" },
    ...over,
  }) as FeaManifestField;

test("row ids round-trip and are told apart by prefix", () => {
  assert.equal(elementSetFromRowId(elementSetRowId("A:b")), "A:b");
  assert.equal(elementSetFromRowId(nodeSetRowId("A")), null);
  assert.deepEqual(propertyValueFromRowId(propertyValueRowId("props.a:b", 12.5)), { field: "props.a:b", value: 12.5 });
  assert.equal(propertyValueFromRowId("fea:propval:props.x:"), null);
  assert.equal(propertyValueFromRowId("fea:propval:props.x:abc"), null);
  assert.ok(isFeaTreeRowId(FEA_SETS_ROOT_ID) && !isFeaTreeRowId("E12"));
  assert.ok(isIsolatableRowId(elementSetRowId("Deck")));
  assert.ok(isIsolatableRowId(propertyValueRowId("props.material", 2)));
  assert.ok(!isIsolatableRowId(nodeSetRowId("Deck")));
  assert.ok(!isIsolatableRowId(FEA_SETS_ROOT_ID));
});

test("sets become one row per name and kind, node sets in their own branch, marked informational", () => {
  const root = buildSetsRoot(SETS, new Set([elementSetRowId("Side")]))!;
  assert.equal(root.id, FEA_SETS_ROOT_ID);
  assert.equal(root.meta, "4");
  const [elements, nodes] = root.children;
  assert.equal(elements.id, FEA_ELEMENT_SETS_ID);
  assert.deepEqual(elements.children.map((c) => [c.name, c.meta, !!c.marked]), [
    ["Deck", "2 el", false],
    ["Side", "2 el", true],
  ]);
  assert.equal(nodes.id, FEA_NODE_SETS_ID);
  assert.ok(nodes.informational);
  assert.deepEqual(nodes.children.map((c) => [c.id, c.meta, c.informational]), [
    [nodeSetRowId("Deck"), "3 n", true],
    [nodeSetRowId("Supports"), "1 n", true],
  ]);
  // Ids are unique across the tree, though a name repeats across kinds.
  const ids = [root, ...root.children, ...root.children.flatMap((c) => c.children)].map((r) => r.id);
  assert.equal(new Set(ids).size, ids.length);
});

test("one kind of set only: no intermediate branch; no sets: no root", () => {
  const root = buildSetsRoot(SETS.slice(0, 2))!;
  assert.deepEqual(root.children.map((c) => c.id), [elementSetRowId("Deck"), elementSetRowId("Side")]);
  assert.equal(buildSetsRoot([]), null);
});

test("a property row reads its values once its membership is in, counted and sorted", () => {
  const material = field({ value_labels: { "1": "Steel B", "2": "Steel A" } });
  assert.equal(propertyLabel(material), "Material");
  const pending = buildPropertyRow(material, undefined, "loading");
  assert.equal(pending.meta, "reading…");
  assert.ok(pending.informational);
  assert.equal(buildPropertyRow(material, undefined, "failed").meta, "unavailable");
  const read = buildPropertyRow(material, new Map([["E1", 1], ["E2", 2], ["E3", 2]]), undefined, new Set([propertyValueRowId("props.material", 2)]));
  assert.deepEqual(read.children.map((c) => [c.name, c.meta, !!c.marked]), [
    ["Steel A", "2 el", true],
    ["Steel B", "1 el", false],
  ]);
  const thickness = field({ name_canonical: "props.thickness", unit: "m" });
  const t = buildPropertyRow(thickness, new Map([["E1", 0.012], ["E2", 0.008], ["E3", 0.01]]), undefined);
  assert.deepEqual(t.children.map((c) => c.name), ["0.008 m", "0.01 m", "0.012 m"], "numerically, with the unit");
  assert.equal(propertyValueLabel(3, field({})), "3");
});

test("the roots: Sets and Properties, each only with something in it", () => {
  const props = propertyFieldsOf([field({}), field({ name_canonical: "stress", category: "stress" }), field({ name_canonical: "props.none", per_type: [] })]);
  assert.deepEqual(props.map((f) => f.name_canonical), ["props.material"]);
  const inputs = { sets: SETS, propertyFields: props, membership: new Map(), membershipStatus: new Map(), isolated: new Set<string>() };
  assert.deepEqual(buildFeaTreeRoots(inputs).map((r) => r.id), [FEA_SETS_ROOT_ID, FEA_PROPERTIES_ROOT_ID]);
  assert.deepEqual(buildFeaTreeRoots({ ...inputs, sets: [] }).map((r) => r.id), [FEA_PROPERTIES_ROOT_ID]);
  assert.deepEqual(buildFeaTreeRoots({ ...inputs, propertyFields: [] }).map((r) => r.id), [FEA_SETS_ROOT_ID]);
});

test("an isolation keeps the union of its sets and values, de-duplicated; node sets name nothing", () => {
  const membership = new Map([["props.material", new Map([["E3", 1], ["E4", 2], ["E5", 1]])]]);
  const rows = [elementSetRowId("Deck"), elementSetRowId("Side"), propertyValueRowId("props.material", 1), nodeSetRowId("Deck")];
  assert.deepEqual(isolatedMembers(rows, SETS, membership), ["E1", "E2", "E3", "E5"]);
  assert.deepEqual(isolatedMembers([nodeSetRowId("Deck"), "fea:sets"], SETS, membership), []);
  assert.deepEqual(isolatedMembers([propertyValueRowId("props.unread", 1)], SETS, membership), []);
});

test("combining isolations: replace, add, remove; only isolatable ids kept", () => {
  const a = elementSetRowId("A");
  const b = elementSetRowId("B");
  assert.deepEqual(combineIsolation([a], [b, nodeSetRowId("N"), b], "replace"), [b]);
  assert.deepEqual(combineIsolation([a], [b, a], "add"), [a, b]);
  assert.deepEqual(combineIsolation([a, b], [a], "remove"), [b]);
  assert.deepEqual(combineIsolation([a], [], "replace"), []);
});

test("sets matching a text; set membership per element", () => {
  assert.deepEqual(setRowsMatching(SETS, " de "), setRowsMatching(SETS, "DE"), "trimmed, case ignored");
  assert.deepEqual(setRowsMatching(SETS, "DE"), [elementSetRowId("Deck"), elementSetRowId("Side")]);
  assert.deepEqual(setRowsMatching(SETS, "supp"), [], "node sets are not isolated");
  assert.deepEqual(setRowsMatching(SETS, "  "), []);
  const by = setsByElement(SETS);
  assert.deepEqual(by.get("E2"), ["Deck", "Side"]);
  assert.equal(by.get("P1"), undefined);
});

test("why the context toggle or the isolation cannot act", () => {
  const base = { loaded: true, hasEdges: true, isolated: [] as string[], members: 0 };
  assert.equal(contextWireframeReason(base), null, "nothing isolated: the choice is kept for the next one");
  assert.equal(contextWireframeReason({ ...base, loaded: false }), "No FEA result is loaded");
  assert.match(contextWireframeReason({ ...base, hasEdges: false })!, /no element edges/);
  assert.match(contextWireframeReason({ ...base, isolated: ["x"], members: 0 })!, /name no elements/);
  assert.equal(contextWireframeReason({ ...base, isolated: ["x"], members: 3 }), null);
  assert.equal(isolationReason({ loaded: false, sets: 1, properties: 1 }), "No FEA result is loaded");
  assert.match(isolationReason({ loaded: true, sets: 0, properties: 0 })!, /no sets or properties/);
  assert.equal(isolationReason({ loaded: true, sets: 0, properties: 1 }), null);
});
