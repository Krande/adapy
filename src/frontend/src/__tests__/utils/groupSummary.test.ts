import assert from "node:assert/strict";
import { test } from "node:test";

import { indexLookups, memberKinds, memberSources } from "@/utils/groups/groupSummary";
import type { GroupMember } from "@/utils/groups/savedGroups";

const node = (collection: string, element: string | null, subject = "site-a"): GroupMember => ({
  target: { kind: "node", provider: "prov", collection, subject, revision: null, node: null },
  element,
  path: element ? [element] : [],
});
const file: GroupMember = { target: { kind: "file", source_key: "models/a.ifc" }, element: "beam", path: ["beam"] };

const members = [node("col", "BEND 1 of BRANCH /R"), node("col", "STRT 1 of BRANCH /R"), node("col", "/PLATE"), node("col", null), file];

test("members are counted by where they come from", () => {
  assert.deepEqual(memberSources(members), [
    { label: "prov · COL", count: 4 },
    { label: "models/a.ifc", count: 1 },
  ]);
});

test("an element is looked up by its full name, a whole node by its id, a file not at all", () => {
  const lookups = indexLookups(members);
  assert.deepEqual([...lookups.keys()], ["col"]);
  assert.deepEqual(lookups.get("col"), { nodeIds: ["site-a"], fullNames: ["BEND 1 of BRANCH /R", "STRT 1 of BRANCH /R", "/PLATE"] });
});

test("kinds come from the index; what it does not answer is unknown", async () => {
  const summary = await memberKinds(members, async () => [
    { node_id: "n1", full_name: "BEND 1 of BRANCH /R", kind: "BEND" },
    { node_id: "n2", full_name: "STRT 1 of BRANCH /R", kind: "STRT" },
    { node_id: "site-a", full_name: "/SITE-A", kind: "SITE" },
  ]);
  assert.deepEqual(summary.kinds, [
    { label: "BEND", count: 1 },
    { label: "SITE", count: 1 },
    { label: "STRT", count: 1 },
  ]);
  assert.equal(summary.unknown, 2, "/PLATE is not in the index, and the file has none");
});

test("a failing index read counts its members as unknown", async () => {
  const summary = await memberKinds(members, async () => {
    throw new Error("down");
  });
  assert.deepEqual(summary, { kinds: [], unknown: 5 });
});
