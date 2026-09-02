import assert from "node:assert/strict";
import { test } from "node:test";

import {
  UI_HIDDEN_PREFIXES,
  isUiHiddenKey,
  partitionUiHidden,
} from "@/utils/storage/fileTree";

// Published-asset blobs are hidden from the file BROWSERS, never from the API.
//
// GET /scopes/{scope}/files is the sole index of the assets/ prefix — no prefix
// filter, no pagination — and the weld-gen-assets plugin builds its whole
// collection -> subject -> revision hierarchy out of that one listing, via its
// own viewerApi.listFiles call. Filtering server-side would not degrade it, it
// would silently blank it. converter.py carries the same warning at the source.

test("assets/ is hidden from the browsers", () => {
  assert.ok(isUiHiddenKey("assets/asp/asp/20260825T135553Z/csg.db"));
  assert.ok(isUiHiddenKey("/assets/leading-slash/x/y/f.db"), "a leading slash must not defeat it");
});

test("ordinary uploads are untouched", () => {
  for (const k of ["models/crane.step", "a/b/c.ifc", "asset-report.pdf", "assets.step"]) {
    assert.ok(!isUiHiddenKey(k), `${k} must stay visible`);
  }
});

test("a path merely containing assets/ is not hidden", () => {
  // The rule is a prefix, not a substring: a user folder called assets is
  // theirs, and hiding it would be exactly the silent loss this guards against.
  assert.ok(!isUiHiddenKey("models/assets/part.step"));
});

test("derived prefixes are not this mechanism's business", () => {
  // _derived/ and friends are dropped server-side by HIDDEN_PREFIXES and never
  // reach a client, so duplicating them here would be dead code pretending to
  // be a safety net.
  assert.deepEqual([...UI_HIDDEN_PREFIXES], ["assets/"]);
});

test("partition keeps both halves so the count can be shown", () => {
  // A browser that hides files without admitting it is indistinguishable from
  // one that lost them.
  const items = [
    { key: "models/a.step" },
    { key: "assets/c/s/r/f.db" },
    { key: "assets/c/s/r/g.db" },
    { key: "b.ifc" },
  ];
  const { visible, hidden } = partitionUiHidden(items, (i) => i.key);
  assert.deepEqual(visible.map((v) => v.key), ["models/a.step", "b.ifc"]);
  assert.equal(hidden.length, 2);
});

test("partition preserves order within each half", () => {
  const items = [{ key: "z.step" }, { key: "a.step" }];
  assert.deepEqual(
    partitionUiHidden(items, (i) => i.key).visible.map((v) => v.key),
    ["z.step", "a.step"],
  );
});
