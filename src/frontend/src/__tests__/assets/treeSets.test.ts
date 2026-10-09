// Tree sets: the stored document (parse, serialise, read-modify-write changes), capturing rows as
// members, per-member provider choices, and the filter that narrows the Sources tree to a set.

import { strict as assert } from "node:assert";
import { test } from "node:test";

import { buildHierarchy } from "../../assets/hierarchy";
import { NO_VIEW, displayHierarchy } from "../../assets/treeView";
import {
  EMPTY_SETS_DOC,
  SETS_SCHEMA,
  applySetsChange,
  bothKeep,
  loadsProvider,
  membersForIds,
  newSetId,
  parseSetsDoc,
  serialiseSetsDoc,
  setMembership,
  setsDocKey,
  treeSetFilter,
  type TreeSet,
  type TreeSetsDoc,
} from "../../assets/treeSets";

// world -> {site-a -> zone-1 -> beam, site-b -> zone-2, site-temp}
const h = buildHierarchy(
  [
    ["world", null, "WORL"],
    ["site-a", "world", "SITE"],
    ["zone-1", "site-a", "ZONE"],
    ["beam", "zone-1", "GENSEC"],
    ["site-b", "world", "SITE"],
    ["zone-2", "site-b", "ZONE"],
    ["site-temp", "world", "SITE"],
  ].map(([id, parent, kind]) => ({
    id: id as string,
    parent: parent as string | null,
    data: { kind: kind as string, label: `/${(id as string).toUpperCase()}` },
  })),
);

const set = (id: string, members: TreeSet["members"]): TreeSet => ({ id, name: id, created_at: "t0", members });
const doc = (...sets: TreeSet[]): TreeSetsDoc => ({ schema: SETS_SCHEMA, sets });
const opts = { searchActive: false, showHidden: false };

test("membership marks each row for editing in the tree: member, covered below one, containing one above", () => {
  const s = set("main", membersForIds(["zone-1", "site-b"], h));
  const of = setMembership(s, h);
  assert.equal(of("zone-1"), "member");
  assert.equal(of("site-b"), "member");
  assert.equal(of("beam"), "covered", "under a member");
  assert.equal(of("zone-2"), "covered");
  assert.equal(of("site-a"), "contains", "on the way down to one");
  assert.equal(of("world"), "contains");
  assert.equal(of("site-temp"), null);
  // Asked again (memoised walks), the answers hold.
  assert.equal(of("beam"), "covered");
  assert.equal(of("site-temp"), null);
});

test("the document lives beside the saved view, one per collection", () => {
  assert.equal(setsDocKey("abc"), "assets/_sets/abc.json");
});

test("empty and absent documents read as no sets; an unknown schema refuses", () => {
  assert.deepEqual(parseSetsDoc(""), EMPTY_SETS_DOC);
  assert.deepEqual(parseSetsDoc(null), EMPTY_SETS_DOC);
  assert.throws(() => parseSetsDoc("{"), /not JSON/);
  assert.throws(() => parseSetsDoc({ schema: "ada.assets/sets@2", sets: [] }), /will not write over/);
});

test("malformed sets and members are dropped; duplicates keep the first", () => {
  const parsed = parseSetsDoc({
    schema: SETS_SCHEMA,
    sets: [
      { id: "s1", name: " Main ", members: [{ id: "site-a", path: ["world"] }, { id: "site-a" }, { label: "no id" }, 7] },
      { id: "s1", name: "dup" },
      { id: "s2", name: "  " },
      { name: "no id" },
    ],
  });
  assert.equal(parsed.sets.length, 1);
  assert.equal(parsed.sets[0].name, "Main");
  assert.deepEqual(parsed.sets[0].members, [{ id: "site-a", label: "site-a", path: ["world"] }]);
});

test("a provider choice round-trips, an empty one included", () => {
  const d = doc(
    set("s1", [
      { id: "site-a", label: "A", path: [], providers: ["p2", "p1", "p1"] },
      { id: "site-b", label: "B", path: [], providers: [] },
      { id: "site-temp", label: "T", path: [] },
    ]),
  );
  const back = parseSetsDoc(serialiseSetsDoc(d)).sets[0].members;
  assert.deepEqual(back[0].providers, ["p2", "p1"]);
  assert.deepEqual(back[1].providers, []);
  assert.equal(back[2].providers, undefined);
  assert.equal(loadsProvider(back[0], "p1"), true);
  assert.equal(loadsProvider(back[1], "p1"), false);
  assert.equal(loadsProvider(back[2], "anything"), true);
});

test("changes apply to the latest document and keep everyone else's sets", () => {
  let d = doc(set("mine", []), set("theirs", [{ id: "site-b", label: "B", path: ["world"] }]));
  d = applySetsChange(d, { kind: "add", id: "mine", members: membersForIds(["site-a"], h), at: "t1" });
  assert.deepEqual(d.sets[0].members.map((m) => m.id), ["site-a"]);
  assert.equal(d.sets[0].updated_at, "t1");
  assert.deepEqual(d.sets[1], set("theirs", [{ id: "site-b", label: "B", path: ["world"] }]));

  d = applySetsChange(d, { kind: "rename", id: "mine", name: "  Main ", at: "t2" });
  assert.equal(d.sets[0].name, "Main");
  assert.equal(applySetsChange(d, { kind: "rename", id: "mine", name: " ", at: "t3" }), d);

  d = applySetsChange(d, { kind: "remove", id: "mine", memberIds: ["site-a"], at: "t4" });
  assert.deepEqual(d.sets[0].members, []);

  d = applySetsChange(d, { kind: "delete", id: "theirs" });
  assert.deepEqual(d.sets.map((s) => s.id), ["mine"]);
  // A change to a set someone else deleted is a no-op, not a resurrection.
  assert.deepEqual(applySetsChange(d, { kind: "add", id: "theirs", members: [], at: "t5" }).sets.map((s) => s.id), ["mine"]);
});

test("provider choices are written in one change; null is every provider again", () => {
  let d = doc(set("s", membersForIds(["site-a", "site-b"], h)));
  d = applySetsChange(d, {
    kind: "providers",
    id: "s",
    choices: [
      { memberId: "site-a", providers: ["p2", "p1"] },
      { memberId: "site-b", providers: [] },
    ],
    at: "t1",
  });
  assert.deepEqual(d.sets[0].members.map((m) => m.providers), [["p1", "p2"], []]);
  d = applySetsChange(d, { kind: "providers", id: "s", choices: [{ memberId: "site-a", providers: null }], at: "t2" });
  assert.equal(d.sets[0].members[0].providers, undefined);
  assert.deepEqual(d.sets[0].members[1].providers, []);
});

test("re-adding a member refreshes its label and path but keeps its provider choice", () => {
  let d = doc(set("s", [{ id: "site-a", label: "old", path: [], providers: ["p1"] }]));
  d = applySetsChange(d, { kind: "add", id: "s", members: membersForIds(["site-a"], h), at: "t1" });
  assert.deepEqual(d.sets[0].members, [{ id: "site-a", label: "/SITE-A", path: ["world"], providers: ["p1"] }]);
});

test("capture stores the label and the ids above, root first, and skips what the tree lacks", () => {
  assert.deepEqual(membersForIds(["beam", "nowhere", "beam"], h), [
    { id: "beam", label: "/BEAM", path: ["world", "site-a", "zone-1"] },
  ]);
});

test("the filter draws members, everything under them and the way down -- nothing beside", () => {
  const f = treeSetFilter(set("s", membersForIds(["site-a"], h)), h);
  const d = displayHierarchy(h, NO_VIEW, { ...opts, keep: f.keep });
  assert.deepEqual(d.hierarchy.roots, ["world"]);
  assert.deepEqual(d.hierarchy.childrenOf("world"), ["site-a"]);
  assert.deepEqual(d.hierarchy.childrenOf("site-a"), ["zone-1"]);
  assert.deepEqual(d.hierarchy.childrenOf("zone-1"), ["beam"]);
  assert.deepEqual(f.missing, []);
});

test("a member deep in a branch keeps only the way down to it", () => {
  const f = treeSetFilter(set("s", membersForIds(["zone-2"], h)), h);
  const d = displayHierarchy(h, NO_VIEW, { ...opts, keep: f.keep });
  assert.deepEqual(d.hierarchy.childrenOf("world"), ["site-b"]);
  assert.deepEqual(d.hierarchy.childrenOf("site-b"), ["zone-2"]);
});

test("a member the tree does not hold is reported, and its stored path still keeps the way down", () => {
  const f = treeSetFilter(set("s", [{ id: "unfetched", label: "/UNFETCHED", path: ["world", "site-temp"] }]), h);
  assert.deepEqual(f.missing.map((m) => m.id), ["unfetched"]);
  const d = displayHierarchy(h, NO_VIEW, { ...opts, keep: f.keep });
  assert.deepEqual(d.hierarchy.childrenOf("world"), ["site-temp"]);
});

test("an empty set draws nothing", () => {
  const f = treeSetFilter(set("s", []), h);
  assert.deepEqual(displayHierarchy(h, NO_VIEW, { ...opts, keep: f.keep }).hierarchy.roots, []);
});

test("two filters combine as both", () => {
  const a = (id: string) => id !== "site-b";
  const b = (id: string) => id !== "site-temp";
  assert.equal(bothKeep(undefined, undefined), undefined);
  assert.equal(bothKeep(a, undefined), a);
  assert.equal(bothKeep(undefined, b), b);
  const both = bothKeep(a, b)!;
  assert.deepEqual(["site-a", "site-b", "site-temp"].map(both), [true, false, false]);
});

test("set ids are time-ordered strings", () => {
  const a = newSetId(1000, () => 0.5);
  const b = newSetId(2000, () => 0.1);
  assert.ok(a < b);
  assert.match(a, /^s[0-9a-z]{15}$/);
});
