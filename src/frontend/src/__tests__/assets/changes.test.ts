// The change feed's four states (`behind | current | not-recorded | no-feed`)
// and the per-node evidence marks -- Phase 4's frontend half of §Decision 4.
//
// Pinned here, and why each pin exists:
//   - the pair that must never collapse: `current` and `not-recorded` read
//     identically to a careless renderer, and are opposite claims.
//   - a 503 (no-feed) answers `no-feed` for every ref asked about it, never a
//     silent `current`.
//   - evidence marks are a positive claim only -- absence from the feed's
//     rows never becomes a mark, whatever the reason for the absence.
//   - "not asked" is a THIRD thing again, apart from all four states: a root
//     the browser never queried gets no entry at all, not a guess.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  classifyChanges,
  evidenceMarks,
  sourceNodesAnswerFromWire,
  type ExportRoot,
  type RootLookup,
  type SourceNodeRow,
  type SourceNodesAnswer,
} from "../../assets/changes";
import type { WireSourceNodesRefsResponse } from "../../assets/types";

const R1 = "20260825T000000Z"; // 2026-08-25T00:00:00Z
const R2 = "20260827T000000Z"; // 2026-08-27T00:00:00Z

function row(nodeRef: string, lastChangedAt: string, opts: Partial<SourceNodeRow> = {}): SourceNodeRow {
  return {
    nodeRef,
    parentRef: opts.parentRef ?? null,
    name: opts.name ?? null,
    lastChangedAt,
    lastChangedBy: opts.lastChangedBy ?? null,
    observedAt: opts.observedAt ?? lastChangedAt,
    action: opts.action ?? null,
  };
}

function answer(source: string, rows: readonly SourceNodeRow[], unknown: readonly string[] = []): SourceNodesAnswer {
  return { source, rows: new Map(rows.map((r) => [r.nodeRef, r])), unknown: new Set(unknown) };
}

/** A lookup where every listed subject was asked, against one fixed answer
 *  (or `null` for no-feed) -- the common case in these tests. */
function askedLookup(a: SourceNodesAnswer | null, asked: readonly string[] = []): RootLookup {
  const askedSet = new Set(asked.length ? asked : a ? [...a.rows.keys()] : []);
  return (subject) => (askedSet.has(subject) ? { asked: true, answer: a } : { asked: false, answer: null });
}

// ---------------------------------------------------------------------------
// the four states
// ---------------------------------------------------------------------------

test("a root the feed recorded a LATER change for than its own revision is `behind`", () => {
  const roots: ExportRoot[] = [{ subject: "site-a", revision: R1 }];
  const a = answer("provider-x", [row("site-a", "2026-08-27T00:00:00Z")]);
  const report = classifyChanges(roots, askedLookup(a, ["site-a"]));
  assert.equal(report.byRoot.get("site-a")?.state, "behind");
  assert.equal(report.behind, 1);
  assert.equal(report.current, 0);
});

test("a root the feed recorded a change for AT OR BEFORE its own revision is `current`", () => {
  const roots: ExportRoot[] = [{ subject: "site-a", revision: R2 }];
  const a = answer("provider-x", [row("site-a", "2026-08-25T00:00:00Z")]);
  const report = classifyChanges(roots, askedLookup(a, ["site-a"]));
  assert.equal(report.byRoot.get("site-a")?.state, "current");
  assert.equal(report.current, 1);
});

test("a root recorded at EXACTLY its own revision instant is current, not behind (strictly greater only)", () => {
  const roots: ExportRoot[] = [{ subject: "site-a", revision: R1 }];
  const a = answer("provider-x", [row("site-a", "2026-08-25T00:00:00Z")]);
  const report = classifyChanges(roots, askedLookup(a, ["site-a"]));
  assert.equal(report.byRoot.get("site-a")?.state, "current");
});

test("`current` and `not-recorded` must never collapse: absence from a GIVEN answer is not-recorded, not current", () => {
  const roots: ExportRoot[] = [
    { subject: "site-a", revision: R1 }, // has a row
    { subject: "site-b", revision: R1 }, // the feed never mentions it
  ];
  const a = answer("provider-x", [row("site-a", "2026-08-24T00:00:00Z")]);
  const report = classifyChanges(roots, askedLookup(a, ["site-a", "site-b"]));
  assert.equal(report.byRoot.get("site-a")?.state, "current");
  assert.equal(report.byRoot.get("site-b")?.state, "not-recorded", "absence from a real answer means nobody looked");
  assert.notEqual(
    report.byRoot.get("site-a")?.state,
    report.byRoot.get("site-b")?.state,
    "the two must read as different states even though neither is bad news",
  );
  assert.equal(report.notRecorded, 1);
});

test("a root never ASKED about gets no entry at all -- not a guessed `not-recorded`", () => {
  const roots: ExportRoot[] = [{ subject: "site-a", revision: R1 }];
  const report = classifyChanges(roots, () => ({ asked: false, answer: null }));
  assert.equal(report.byRoot.has("site-a"), false);
  assert.equal(report.notRecorded, 0, "an unasked root must not inflate the not-recorded count either");
});

test("a 503 answer (no-feed) yields `no-feed` for every asked ref, never `current`", () => {
  const roots: ExportRoot[] = [
    { subject: "site-a", revision: R1 },
    { subject: "site-b", revision: R2 },
  ];
  const report = classifyChanges(roots, askedLookup(null, ["site-a", "site-b"]));
  assert.equal(report.byRoot.get("site-a")?.state, "no-feed");
  assert.equal(report.byRoot.get("site-b")?.state, "no-feed");
  assert.equal(report.behind, 0);
  assert.equal(report.current, 0);
  assert.equal(report.notRecorded, 0, "no-feed is its own bucket, not folded into not-recorded's count");
});

test("no-feed is per-provider: one provider's roots read no-feed while another's are classified normally", () => {
  const roots: ExportRoot[] = [
    { subject: "site-a", revision: R1 },
    { subject: "site-b", revision: R1 },
  ];
  const providerOf: Record<string, string> = { "site-a": "provider-down", "site-b": "provider-up" };
  const upAnswer = answer("provider-up", [row("site-b", "2026-08-24T00:00:00Z")]);
  const lookup: RootLookup = (subject) => {
    if (providerOf[subject] === "provider-down") return { asked: true, answer: null };
    return { asked: true, answer: upAnswer };
  };
  const report = classifyChanges(roots, lookup);
  assert.equal(report.byRoot.get("site-a")?.state, "no-feed");
  assert.equal(report.byRoot.get("site-b")?.state, "current");
});

test("a malformed last_changed_at is read as current rather than thrown", () => {
  const roots: ExportRoot[] = [{ subject: "site-a", revision: R1 }];
  const a = answer("provider-x", [row("site-a", "not-a-timestamp")]);
  assert.doesNotThrow(() => classifyChanges(roots, askedLookup(a, ["site-a"])));
  assert.equal(classifyChanges(roots, askedLookup(a, ["site-a"])).byRoot.get("site-a")?.state, "current");
});

test("classifyChanges over no roots is the empty report, not an error", () => {
  const report = classifyChanges([], () => ({ asked: false, answer: null }));
  assert.equal(report.byRoot.size, 0);
  assert.equal(report.behind, 0);
});

// ---------------------------------------------------------------------------
// per-node evidence marks
// ---------------------------------------------------------------------------

test("evidence marks appear only for refs the feed actually returned with an action", () => {
  const changedRows = new Map([
    ["member-3", row("member-3", "2026-08-27T00:00:00Z", { action: "modified" })],
    ["member-7", row("member-7", "2026-08-27T00:00:00Z", { action: "added" })],
    // A roll-up ancestor row with no action is not evidence at THIS node --
    // callers keep it out of `changedRows` (the store filters on `action`
    // before this ever runs), but this module must not invent a mark for it
    // even if one slipped through.
    ["level-2", row("level-2", "2026-08-27T00:00:00Z", { action: null })],
  ]);
  const marks = evidenceMarks(changedRows);
  assert.equal(marks.get("member-3"), "modified");
  assert.equal(marks.get("member-7"), "added");
  assert.equal(marks.has("level-2"), false, "no action recorded here -- absence, not a guessed mark");
  assert.equal(marks.has("area-1"), false, "never asked about -- nothing to mark");
});

test("evidence marks over an empty map is the empty map", () => {
  assert.equal(evidenceMarks(new Map()).size, 0);
});

// ---------------------------------------------------------------------------
// wire parsing
// ---------------------------------------------------------------------------

test("sourceNodesAnswerFromWire parses rows by node_ref and keeps `unknown` apart", () => {
  const wire: WireSourceNodesRefsResponse = {
    scope: "user:me",
    source: "provider-x",
    nodes: [
      {
        node_ref: "site-a",
        parent_ref: null,
        name: "Site A",
        last_changed_at: "2026-08-27T00:00:00Z",
        last_changed_by: "alice",
        observed_at: "2026-08-27T00:05:00Z",
        action: "modified",
      },
    ],
    unknown: ["site-b"],
  };
  const a = sourceNodesAnswerFromWire(wire);
  assert.equal(a.source, "provider-x");
  assert.equal(a.rows.size, 1);
  const r = a.rows.get("site-a")!;
  assert.equal(r.lastChangedBy, "alice");
  assert.equal(r.action, "modified");
  assert.ok(a.unknown.has("site-b"));
});

test("sourceNodesAnswerFromWire defaults an absent action to null, never a guessed value", () => {
  const wire: WireSourceNodesRefsResponse = {
    scope: "user:me",
    source: "provider-x",
    nodes: [
      {
        node_ref: "site-a",
        parent_ref: null,
        name: null,
        last_changed_at: "2026-08-27T00:00:00Z",
        last_changed_by: null,
        observed_at: "2026-08-27T00:00:00Z",
        // no `action` at all -- a deployment that has not migrated the column in
      },
    ],
    unknown: [],
  };
  const a = sourceNodesAnswerFromWire(wire);
  assert.equal(a.rows.get("site-a")?.action, null);
});
