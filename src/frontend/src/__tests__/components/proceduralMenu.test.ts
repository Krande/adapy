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

test("a handler that is not supplied yields no entry", () => {
  // Otherwise the menu would show an action that does nothing when clicked.
  const items = buildProceduralMenuItems("module-a", { canMutate: true, onOpen: noop });
  assert.deepEqual(items.map((i) => i.key), ["open"]);
});
