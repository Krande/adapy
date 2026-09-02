import assert from "node:assert/strict";
import { test } from "node:test";

import { buildProceduralMenuItems } from "@/components/storage/storageMenuItems";

// A procedural model is filed beside real files in the storage tree, so "how do
// I move this" must not have a different answer depending on what the row is
// backed by. Same kebab, same right-click, same Rename/Move wording.

const noop = () => {};
const full = () =>
  buildProceduralMenuItems("module-a", {
    canMutate: true,
    onOpen: noop,
    onMakeActive: noop,
    onDeactivate: noop,
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

test("open is first, because the row's primary action is opening it", () => {
  assert.equal(full()[0].key, "open");
});

test("it offers NO file-only actions", () => {
  // A model is a database row: load into the scene, download and convert would
  // promise operations that cannot work on it.
  const keys = full().map((i) => i.key);
  for (const k of ["toggle-load", "load-streamer", "download"]) {
    assert.ok(!keys.includes(k), `${k} must not be offered on a model`);
  }
});

test("delete is destructive and separated", () => {
  const del = full().find((i) => i.key === "delete");
  assert.equal(del?.destructive, true);
  assert.equal(del?.separatorBefore, true, "delete must not sit flush against move");
});

test("a read-only scope keeps Open and Copy but loses the mutations", () => {
  const items = buildProceduralMenuItems("module-a", {
    canMutate: false,
    onOpen: noop,
    onCopyPath: noop,
    onRename: noop,
    onMoveToFolder: noop,
    onDelete: noop,
  });
  const keys = items.map((i) => i.key);
  assert.deepEqual(keys, ["open", "copy-path"]);
});

// ── active vs in-the-scene ─────────────────────────────────────────

test("an inactive model offers Make active", () => {
  const keys = full().map((i) => i.key);
  assert.ok(keys.includes("make-active"));
  assert.ok(!keys.includes("deactivate"), "cannot offer both at once");
});

test("the active model offers Stop editing instead", () => {
  // Exactly one model is active, so the entry is a toggle rather than a
  // command that could be issued twice.
  const items = buildProceduralMenuItems("module-a", {
    canMutate: true,
    isActive: true,
    onOpen: noop,
    onMakeActive: noop,
    onDeactivate: noop,
  });
  const keys = items.map((i) => i.key);
  assert.ok(keys.includes("deactivate"));
  assert.ok(!keys.includes("make-active"));
});

test("stopping editing does not claim to unload the scene", () => {
  // Several results can sit in the scene at once; deactivating edits one
  // document and must not imply it removes anything.
  const items = buildProceduralMenuItems("module-a", {
    canMutate: true,
    isActive: true,
    onOpen: noop,
    onDeactivate: noop,
  });
  const d = items.find((i) => i.key === "deactivate");
  assert.match(String(d?.label), /keep in scene/i);
});

test("a model that never compiled offers no View compiled result", () => {
  // The entry appears only when there is a derived key to load; otherwise it
  // would be a button with nothing behind it.
  const items = buildProceduralMenuItems("module-a", {
    canMutate: true,
    onOpen: noop,
    onMakeActive: noop,
  });
  assert.ok(!items.map((i) => i.key).includes("view-result"));
});

test("View compiled result is offered without making the model active", () => {
  // This is what "several in the scene, one being edited" is made of.
  const keys = full().map((i) => i.key);
  assert.ok(keys.includes("view-result"));
  assert.ok(keys.includes("make-active"), "the two must be separate choices");
});

test("a handler that is not supplied yields no entry", () => {
  // Otherwise the menu would show an action that does nothing when clicked.
  const items = buildProceduralMenuItems("module-a", { canMutate: true, onOpen: noop });
  assert.deepEqual(items.map((i) => i.key), ["open"]);
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

test("loadable count excludes models", () => {
  // A selection of only models must not offer an enabled Load that does
  // nothing when clicked.
  const modelNames = new Set(["m1", "m2"]);
  const selection = ["m1", "m2", "a.step"];
  const selectedModelCount = selection.filter((k) => modelNames.has(k)).length;
  assert.equal(selection.length - selectedModelCount, 1);

  const onlyModels = ["m1", "m2"];
  assert.equal(onlyModels.length - onlyModels.filter((k) => modelNames.has(k)).length, 0);
});
