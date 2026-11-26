// Core's own section MATCH rules (ObjectInfoBoxComponent.tsx used to compute these inline;
// coreSections/match.ts holds them now), table-driven: every snapshot kind x selectionSource
// combination against every rule, with no rendering at all.

import assert from "node:assert/strict";
import { test } from "node:test";

// Enough of a browser for the store modules CORE_PROPERTY_PROVIDERS pulls in to load under
// `node --test` (see the last test below, which imports ObjectInfoBoxComponent).
await import("../fixtures/panelRealm");

import type { SelectionSnapshot } from "@/plugins/propertyProviders";
import { MATCH, builderPick, notCell, sceneConceptPick } from "@/components/info_box_selected_object/coreSections/match";

const base = (patch: Partial<SelectionSnapshot>): SelectionSnapshot => ({
  kind: "none",
  name: null,
  meshName: null,
  count: 0,
  hasEntities: false,
  cellBuilderActive: false,
  ...patch,
});

const KINDS: SelectionSnapshot["kind"][] = ["none", "mesh", "cell", "ref"];
const SOURCES: Array<SelectionSnapshot["selectionSource"]> = ["any", "builder", "scene", undefined];

test("notCell: true for none/mesh, false for cell/ref, whatever the selectionSource", () => {
  for (const kind of KINDS) {
    for (const selectionSource of SOURCES) {
      const want = kind !== "cell" && kind !== "ref";
      assert.equal(notCell(base({ kind, selectionSource })), want, `${kind}/${selectionSource}`);
    }
  }
});

test("sceneConceptPick: notCell AND selectionSource !== 'builder'", () => {
  const table: [SelectionSnapshot["kind"], SelectionSnapshot["selectionSource"], boolean][] = [
    ["none", "any", true],
    ["none", "scene", true],
    ["none", "builder", false],
    ["none", undefined, true],
    ["mesh", "any", true],
    ["mesh", "builder", false],
    ["cell", "any", false],
    ["cell", "scene", false],
    ["cell", "builder", false],
    ["ref", "any", false],
  ];
  for (const [kind, selectionSource, want] of table) {
    assert.equal(sceneConceptPick(base({ kind, selectionSource })), want, `${kind}/${selectionSource}`);
  }
});

test("builderPick: a cell or ref selection, only while cellBuilderActive", () => {
  for (const kind of KINDS) {
    const active = builderPick(base({ kind, cellBuilderActive: true }));
    const inactive = builderPick(base({ kind, cellBuilderActive: false }));
    const want = kind === "cell" || kind === "ref";
    assert.equal(active, want, `active/${kind}`);
    assert.equal(inactive, false, `inactive/${kind}`);
  }
});

test("MATCH covers every core provider id, in CORE_PROPERTY_PROVIDERS' own order", async () => {
  const { CORE_PROPERTY_PROVIDERS } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");
  for (const p of CORE_PROPERTY_PROVIDERS) assert.equal(typeof MATCH[p.id], "function", p.id);
  assert.deepEqual(Object.keys(MATCH), CORE_PROPERTY_PROVIDERS.map((p) => p.id));
});

test("selection-summary: shown for anything but an empty 'none', or an empty scene with entities", () => {
  assert.equal(MATCH["selection-summary"](base({ kind: "none", hasEntities: false })), false);
  assert.equal(MATCH["selection-summary"](base({ kind: "none", hasEntities: true })), true, "Unhide all/Fit all stay reachable");
  for (const kind of ["mesh", "cell", "ref"] as const) assert.equal(MATCH["selection-summary"](base({ kind })), true);
});

test("clash-contact / fem-concepts / tree-node: sceneConceptPick, not kind === 'mesh'", () => {
  for (const id of ["clash-contact", "fem-concepts", "tree-node"]) {
    assert.equal(MATCH[id](base({ kind: "none" })), true, `${id}: shows on a stale pick even with kind none`);
    assert.equal(MATCH[id](base({ kind: "none", selectionSource: "builder" })), false, `${id}: not in a builder-only panel`);
    assert.equal(MATCH[id](base({ kind: "cell", cellBuilderActive: true })), false, `${id}: never under a builder pick`);
  }
});

test("source-jump / indexed-attributes / related: notCell", () => {
  for (const id of ["source-jump", "indexed-attributes", "related"]) {
    assert.equal(MATCH[id](base({ kind: "mesh" })), true, id);
    assert.equal(MATCH[id](base({ kind: "cell" })), false, id);
  }
});

test("object-metadata: any non-null meshName, cell pick included", () => {
  assert.equal(MATCH["object-metadata"](base({ kind: "none", meshName: null })), false);
  assert.equal(MATCH["object-metadata"](base({ kind: "cell", meshName: "Room" })), true);
  assert.equal(MATCH["object-metadata"](base({ kind: "mesh", meshName: "E1" })), true);
});

test("fea-element / fea-sets: a mesh pick whose name matches /^E\\d+$/", () => {
  for (const id of ["fea-element", "fea-sets"]) {
    assert.equal(MATCH[id](base({ kind: "mesh", meshName: "E42" })), true, id);
    assert.equal(MATCH[id](base({ kind: "mesh", meshName: "Beam1" })), false, id);
    assert.equal(MATCH[id](base({ kind: "cell", meshName: "E42" })), false, `${id}: not a cell pick`);
  }
});

test("element-links: notCell with at least one node", () => {
  assert.equal(MATCH["element-links"](base({ kind: "mesh", nodes: [{ key: "k" }] })), true);
  assert.equal(MATCH["element-links"](base({ kind: "mesh", nodes: [] })), false);
  assert.equal(MATCH["element-links"](base({ kind: "mesh" })), false);
  assert.equal(MATCH["element-links"](base({ kind: "cell", nodes: [{ key: "k" }] })), false);
});

test("cellbuilder-item / cellbuilder-structure: mutually exclusive on kind", () => {
  assert.equal(MATCH["cellbuilder-item"](base({ kind: "cell", cellBuilderActive: true })), true);
  assert.equal(MATCH["cellbuilder-item"](base({ kind: "ref", cellBuilderActive: true })), true);
  assert.equal(MATCH["cellbuilder-item"](base({ kind: "none", cellBuilderActive: true })), false);
  assert.equal(MATCH["cellbuilder-structure"](base({ kind: "none", cellBuilderActive: true, structure: "Hull" })), true);
  assert.equal(MATCH["cellbuilder-structure"](base({ kind: "none", cellBuilderActive: true })), false, "no active structure");
  assert.equal(MATCH["cellbuilder-structure"](base({ kind: "cell", cellBuilderActive: true, structure: "Hull" })), false, "a pick wins over the active-structure fallback");
});
