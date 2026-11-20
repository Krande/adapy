// The storage browser's "No files yet" is for a scope with nothing to list: a procedural model is
// listed like a file, so a scope with a model and no files shows the model.

import assert from "node:assert/strict";
import { test } from "node:test";

const { storageListEmpty } = await import("@/components/storage/browser/listEmpty");

const none = { files: [], rows: [], pendingFolders: [], newFolderAt: null };

test("nothing at all: empty", () => {
  assert.equal(storageListEmpty(none), true);
});

test("a procedural model and no files: not empty, the model is listed", () => {
  assert.equal(storageListEmpty({ ...none, rows: [{ name: "test" }] }), false);
});

test("a file, a folder made this session, or one being named: not empty", () => {
  assert.equal(storageListEmpty({ ...none, files: [{}], rows: [{}] }), false);
  assert.equal(storageListEmpty({ ...none, pendingFolders: ["new"] }), false);
  assert.equal(storageListEmpty({ ...none, newFolderAt: "" }), false);
});
