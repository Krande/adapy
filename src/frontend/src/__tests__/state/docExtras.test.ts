// A commit writes back what the builder does not own. It wrote only its own keys, so the first edit
// committed from the viewer dropped an engine's own lists from the model (a run tool's
// `modifications` and `framework_modifications`, its `engine`), and the next Save read them as
// removed. `structures` is the one exception: it is a legacy, builder-owned shape (groupsFromDoc's
// fallback converts it to `groups` on load), so -- unlike a genuine engine extra -- it is NOT
// carried back unchanged; a commit writes `groups` instead and drops it.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { BUILDER_DOC_KEYS, docExtras } = await import("@/state/cellbuilder/docCodec");

const modifications = [
  { NAME: "roof_thick", STRUCTURE_NAME: "Mini", INCLUDE: true, SPACE_NAME: "deck", SPACE_SIDE: "Z", CONFIG_NAME: "deck_thick" },
];
const doc = {
  schema_version: "1.1",
  engine: "pm-engine",
  grid: {},
  blueprint: { pm_config: [{ type: "TopoStructuresConfig", data: { NAME: "Mini" } }] },
  spaces: [{ NAME: "deck", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, STRUCTURE_NAME: "Mini" }],
  equipments: [],
  groups: [{ name: "Mini", blueprint: "Stiffened plate" }],
  modifications,
  framework_modifications: [],
  structures: [{ NAME: "Mini", X: 10 }],
} as unknown as ProceduralDoc;

const st = () => useCellBuilderStore.getState();

test("what the builder does not own goes back as it came, through an edit", () => {
  st().open("m", "Model", 1, doc);
  const id = Object.keys(st().cells)[0];
  st().renameCell(id, "deck_2");
  const out = st().toDoc() as unknown as Record<string, unknown>;
  assert.deepEqual(out.modifications, modifications);
  assert.deepEqual(out.framework_modifications, []);
  assert.equal(out.engine, "pm-engine");
  assert.equal("structures" in out, false, "the legacy shape is dropped; groups is written instead");
  assert.deepEqual(out.groups, [{ name: "Mini", blueprint: "Stiffened plate" }]);
  assert.equal((out.spaces as { NAME: string }[])[0].NAME, "deck_2", "the builder's own keys are its own");
});

test("a key the builder owns is never resurrected from the loaded doc", () => {
  st().open("m", "Model", 1, doc);
  st().removeGroup("Mini");
  const out = st().toDoc() as unknown as Record<string, unknown>;
  assert.equal("groups" in out, false, "no groups left: none written, not the loaded ones");
  assert.equal("structures" in out, false, "nor the legacy structures it predates");
  assert.deepEqual(Object.keys(docExtras(doc)).sort(), ["engine", "framework_modifications", "modifications"]);
  for (const k of ["groups", "loft_members", "detailing", "spaces", "structures"]) assert.ok(BUILDER_DOC_KEYS.has(k), k);
});

test("closing forgets them; another doc brings its own", () => {
  st().open("m", "Model", 1, doc);
  st().close();
  assert.deepEqual(st().docExtras, {});
  st().open("n", "Other", 1, { grid: {}, blueprint: {}, spaces: [], equipments: [] } as unknown as ProceduralDoc);
  assert.equal("modifications" in (st().toDoc() as object), false);
});

test("setDocExtra: a shell's edit of an engine's list is one undo step, dirties the model, and is committed", () => {
  st().open("m", "Model", 1, doc);
  assert.equal(st().dirty, false);
  const added = [...modifications, { NAME: "mod2", STRUCTURE_NAME: "Mini", INCLUDE: true, SPACE_NAME: "deck" }];
  assert.equal(st().setDocExtra("modifications", added), true);
  assert.equal(st().dirty, true);
  assert.deepEqual((st().toDoc() as unknown as Record<string, unknown>).modifications, added);
  st().undo();
  assert.deepEqual(st().docExtras.modifications, modifications, "undone");
  st().redo();
  assert.deepEqual(st().docExtras.modifications, added, "redone");
  st().setDocExtra("framework_modifications", undefined);
  assert.equal("framework_modifications" in (st().toDoc() as object), false, "undefined removes the key");
});

test("setDocExtra refuses a key the builder owns", () => {
  st().open("m", "Model", 1, doc);
  assert.equal(st().setDocExtra("spaces", []), false);
  assert.equal(st().dirty, false);
});
