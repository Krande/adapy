// A group loads ONE model per source: members sharing a published node or a file load it once.

import assert from "node:assert/strict";
import { test } from "node:test";

import { groupSourceKey, sourcesToLoad } from "@/utils/groups/groupSources";

const node = (subject: string, nodeId: string | null, element: string | null = null) => ({
  target: { kind: "node" as const, provider: "p", collection: "c", subject, revision: null, node: nodeId },
  element,
  path: element ? ["level"] : [],
});
const file = (key: string, element: string | null = null) => ({
  target: { kind: "file" as const, source_key: key },
  element,
  path: element ? ["level"] : [],
});

test("members of one node or one file share a source key", () => {
  assert.equal(groupSourceKey(node("s", null, "a")), groupSourceKey(node("s", null, "b")));
  assert.equal(groupSourceKey(node("s", "s")), groupSourceKey(node("s", null)), "node defaults to the subject");
  assert.notEqual(groupSourceKey(node("s", "n1")), groupSourceKey(node("s", "n2")));
  assert.equal(groupSourceKey(file("m.ifc", "x")), groupSourceKey(file("m.ifc", "y")));
  assert.notEqual(groupSourceKey(file("m.ifc")), groupSourceKey(node("m.ifc", null)));
});

test("sourcesToLoad folds members into one entry per source", () => {
  const out = sourcesToLoad([node("s", null, "a"), node("s", null, "b"), file("m.ifc"), node("t", "n")]);
  assert.equal(out.size, 3);
  assert.equal(out.get(groupSourceKey(node("s", null)))?.length, 2);
});
