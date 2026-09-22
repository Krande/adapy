// THE THIRD DIRECTION: a role rooted beneath a node, announced on the node.
//
// `rooted` and `covered` both answer from a node's own line — what is here,
// what reaches me from above. Neither is visible until the row that HAS the
// publish is on screen, so a collapsed area with a publish three levels down
// looked exactly like an area with nothing in it. The only way to tell was to
// open every branch, which is the work `below` exists to save.
//
// Ported verbatim (same API) from coverageBelow.test.ts.

import assert from "node:assert/strict";
import { test } from "node:test";

import { buildHierarchy } from "../../assets/hierarchy";
import { projectCoverage } from "../../assets/coverage";

/**  area-1
 *    ├── level-a ── frame ── (content rooted here)
 *    └── level-b               (nothing)          */
const H = buildHierarchy([
  { id: "area-1", parent: null, data: { leaf: false } },
  { id: "level-a", parent: "area-1", data: { leaf: false } },
  { id: "frame", parent: "level-a", data: { leaf: true } },
  { id: "level-b", parent: "area-1", data: { leaf: true } },
]);

const PROPAGATING = new Set(["content", "extra"]);

const cover = (rootedRoles: Map<string, Set<string>>) =>
  projectCoverage(H, {
    rootedRoles,
    publishedSubjects: [...rootedRoles.keys()],
    propagating: PROPAGATING,
    payloadOf: (n) => ((n.data as { leaf: boolean }).leaf ? 1 : 0),
  });

test("a publish deep in a branch is announced all the way up", () => {
  const cov = cover(new Map([["frame", new Set(["content"])]]));
  assert.ok(cov.byId.get("area-1")?.below.has("content"), "the area says something is under it");
  assert.ok(cov.byId.get("level-a")?.below.has("content"), "and so does the level between");
});

test("it is announced only on the branch that holds it", () => {
  const cov = cover(new Map([["frame", new Set(["content"])]]));
  assert.equal(cov.byId.get("level-b")?.below.size, 0, "a sibling branch claims nothing");
});

test("`below` names the shallowest descendant, which is the one to open", () => {
  // Both the level and the frame under it root a publish. The area should
  // point at the level: it is the first thing you would meet going down, and
  // the frame is reachable from there.
  const cov = cover(
    new Map([
      ["level-a", new Set(["content"])],
      ["frame", new Set(["content"])],
    ]),
  );
  assert.equal(cov.byId.get("area-1")?.belowBy.get("content"), "level-a");
});

test("a role answered at this row is not also announced from below", () => {
  // Weights are exclusive. "It is here" is the stronger and more useful claim,
  // and two badges for one role on one row reads as two publishes.
  const cov = cover(
    new Map([
      ["area-1", new Set(["content"])],
      ["frame", new Set(["content"])],
    ]),
  );
  const area1 = cov.byId.get("area-1");
  assert.ok(area1?.rooted.has("content"));
  assert.equal(area1?.below.has("content"), false);
});

test("a role covered from above is not announced from below either", () => {
  // level-a inherits the area's content as a ghost. Adding a `below` for the
  // frame beneath it would put two marks on one row for one publish.
  const cov = cover(
    new Map([
      ["area-1", new Set(["content"])],
      ["frame", new Set(["content"])],
    ]),
  );
  const level = cov.byId.get("level-a");
  assert.ok(level?.covered.has("content"), "the ghost is the row's answer");
  assert.equal(level?.below.has("content"), false);
});

test("only propagating roles travel upward", () => {
  // A non-propagating role is rooted at every root of a sweep. If it climbed,
  // every ancestor of everything would wear it and it would distinguish
  // nothing — the same argument that keeps it out of the downward pass.
  const cov = cover(new Map([["frame", new Set(["tree"])]]));
  assert.equal(cov.byId.get("area-1")?.below.size, 0);
});

test("a tree with no publishes at all announces nothing anywhere", () => {
  const cov = cover(new Map());
  for (const id of H.order) assert.equal(cov.byId.get(id)?.below.size, 0, id);
});

// ---------------------------------------------------------------------------
// gap is counted PER PAYLOAD (`uncoveredSubtree`), not decided per row.
// ---------------------------------------------------------------------------

/**  branch
 *    ├── leaf-a
 *    └── leaf-b   */
const LEAVES = buildHierarchy([
  { id: "branch", parent: null, data: { leaf: false } },
  { id: "leaf-a", parent: "branch", data: { leaf: true } },
  { id: "leaf-b", parent: "branch", data: { leaf: true } },
]);

const coverLeaves = (rootedRoles: Map<string, Set<string>>) =>
  projectCoverage(LEAVES, {
    rootedRoles,
    publishedSubjects: [...rootedRoles.keys()],
    propagating: PROPAGATING,
    payloadOf: (n) => ((n.data as { leaf: boolean }).leaf ? 1 : 0),
  });

test("a branch whose every leaf is published leaf-by-leaf is NOT a gap, though nothing is published at the branch itself", () => {
  const cov = coverLeaves(
    new Map([
      ["leaf-a", new Set(["content"])],
      ["leaf-b", new Set(["content"])],
    ]),
  );
  const branch = cov.byId.get("branch");
  assert.equal(branch?.gap, false);
  assert.equal(branch?.uncoveredSubtree, 0);
  assert.equal(branch?.dimmed, false, "there IS payload here, just none of it uncovered");
});

test("a branch with one uncovered leaf among covered ones is a gap of exactly that leaf", () => {
  const cov = coverLeaves(new Map([["leaf-a", new Set(["content"])]]));
  const branch = cov.byId.get("branch");
  assert.equal(branch?.gap, true);
  assert.equal(branch?.uncoveredSubtree, 1);
});
