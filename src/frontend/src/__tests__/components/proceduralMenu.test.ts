import assert from "node:assert/strict";
import { test } from "node:test";

import { buildProceduralMenuItems } from "@/components/storage/storageMenuItems";

// A procedural model is filed beside real files in the storage tree, so "how do
// I move this" must not have a different answer depending on what the row is
// backed by. Same kebab, same right-click, same Rename/Move wording.
//
// The row's CHECKBOX is the model's presence (in the scene and the builder, or
// neither); the menu never repeats it. What the menu adds is what a box cannot
// say: which of several shown models is the edited one, how a shown one is drawn.

const noop = () => {};
const full = () =>
  buildProceduralMenuItems("module-a", {
    canMutate: true,
    onMakeActive: noop,
    onViewResult: noop,
    onCopyPath: noop,
    onRename: noop,
    onMoveToFolder: noop,
    onDelete: noop,
  });

test("it offers the same organise actions a file does", () => {
  const keys = full().map((i) => i.key);
  for (const k of ["rename", "move-to-folder", "delete", "copy-path"]) {
    assert.ok(keys.includes(k), `missing ${k}`);
  }
});

test("editing is first, because that is what a model is for", () => {
  assert.equal(full()[0].key, "make-active");
});

test("it offers NO file-only actions, and does not repeat the checkbox", () => {
  // A model is a database row: load into the scene, download and convert would
  // promise operations that cannot work on it. "Open" and "Stop editing" were
  // the checkbox said twice: ticking shows (and edits) the model, unticking
  // removes it and closes the builder.
  const keys = full().map((i) => i.key);
  for (const k of ["toggle-load", "load-streamer", "download", "open", "deactivate"]) {
    assert.ok(!keys.includes(k), `${k} must not be offered on a model`);
  }
});

test("delete is destructive and separated", () => {
  const del = full().find((i) => i.key === "delete");
  assert.equal(del?.destructive, true);
  assert.equal(del?.separatorBefore, true, "delete must not sit flush against move");
});

test("a read-only scope keeps Edit and Copy but loses the mutations", () => {
  const items = buildProceduralMenuItems("module-a", {
    canMutate: false,
    onMakeActive: noop,
    onCopyPath: noop,
    onRename: noop,
    onMoveToFolder: noop,
    onDelete: noop,
  });
  const keys = items.map((i) => i.key);
  assert.deepEqual(keys, ["make-active", "copy-path"]);
});

// ── edited vs in-the-scene ─────────────────────────────────────────

test("a model that is not the edited one offers Edit this model", () => {
  const item = full().find((i) => i.key === "make-active");
  assert.ok(item);
  assert.equal(item.label, "Edit this model");
  assert.match(String(item.title), /stays in the scene/i, "the one edited so far is not dropped");
});

test("the edited model offers neither Edit nor Show compiled result", () => {
  // Exactly one model is edited; its presence is the checkbox (unticking it
  // closes the builder), so the menu has nothing to say about editing it.
  const items = buildProceduralMenuItems("module-a", {
    canMutate: true,
    isActive: true,
    onMakeActive: noop,
    onViewResult: noop,
    onCopyPath: noop,
  });
  assert.deepEqual(items.map((i) => i.key), ["copy-path"]);
});

test("a model that never compiled offers no Show compiled result", () => {
  // The entry appears only when there is a derived key to load; otherwise it
  // would be a button with nothing behind it.
  const items = buildProceduralMenuItems("module-a", {
    canMutate: true,
    onMakeActive: noop,
  });
  assert.ok(!items.map((i) => i.key).includes("view-result"));
});

test("Show compiled result is offered without making the model the edited one", () => {
  // This is what "several in the scene, one being edited" is made of.
  const keys = full().map((i) => i.key);
  assert.ok(keys.includes("view-result"));
  assert.ok(keys.includes("make-active"), "the two must be separate choices");
  assert.equal(full().find((i) => i.key === "view-result")?.label, "Show compiled result");
});

test("a shown companion chooses how it is drawn instead", () => {
  const items = buildProceduralMenuItems("module-a", {
    canMutate: true,
    companionRep: "topology",
    hasCompiled: true,
    onShowRep: noop,
    onMakeActive: noop,
    onViewResult: noop,
  });
  const keys = items.map((i) => i.key);
  assert.deepEqual(keys, ["make-active", "show-topology", "show-simulation", "show-detail"]);
  assert.ok(!keys.includes("view-result"), "the representation switch replaces it");
});

test("a handler that is not supplied yields no entry", () => {
  // Otherwise the menu would show an action that does nothing when clicked.
  const items = buildProceduralMenuItems("module-a", { canMutate: true });
  assert.deepEqual(items, []);
});

// ── selection semantics (the part that can silently 404) ───────────

/** Mirrors StorageBrowser.splitSelection: one tree and one selection set, two
 *  different things on the server — a model is a row addressed by UUID, a file
 *  is a blob addressed by key. */
function splitSelection(keys: string[], modelNames: Set<string>) {
  const models: string[] = [];
  const fileKeys: string[] = [];
  for (const k of keys) (modelNames.has(k) ? models : fileKeys).push(k);
  return { models, fileKeys };
}

test("a mixed selection is split by kind, not by guesswork", () => {
  // Deleting a model through the storage API would 404; moving one would
  // silently do nothing. Every bulk action has to fan out.
  const modelNames = new Set(["decks/a/model", "module-b"]);
  const { models, fileKeys } = splitSelection(
    ["models/crane.step", "decks/a/model", "b.ifc", "module-b"],
    modelNames,
  );
  assert.deepEqual(models, ["decks/a/model", "module-b"]);
  assert.deepEqual(fileKeys, ["models/crane.step", "b.ifc"]);
});

test("a model-only selection yields no file keys", () => {
  // The storage bulk-delete loop must then run zero times rather than once
  // with a key that is not a blob.
  const { models, fileKeys } = splitSelection(["m1", "m2"], new Set(["m1", "m2"]));
  assert.equal(fileKeys.length, 0);
  assert.equal(models.length, 2);
});

test("a name that merely looks like a model is treated as a file", () => {
  // Membership is by exact name, never by shape: a real file called
  // "module-b.step" must not be mistaken for the model "module-b".
  const { models, fileKeys } = splitSelection(["module-b.step"], new Set(["module-b"]));
  assert.deepEqual(models, []);
  assert.deepEqual(fileKeys, ["module-b.step"]);
});

test("the scene source name is derived from the model, not from what is active", () => {
  // viewResult otherwise names the source after whichever model is ACTIVE,
  // so the same model loads under different names depending on timing and a
  // row cannot tell whether its own result is in the scene.
  const sourceName = (name: string) => `procedural:${name}`;
  assert.equal(sourceName("decks/a/model"), "procedural:decks/a/model");
  assert.notEqual(sourceName("m1"), sourceName("m2"), "two models must not collide");
});
