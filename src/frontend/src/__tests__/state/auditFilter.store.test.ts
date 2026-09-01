import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AUDIT_FILTER_KEYS,
  countActiveAuditFilters,
  useAuditFilterStore,
} from "@/state/auditFilterStore";

// The Audit tab's filter is shared: Overview counts a population, the operator
// clicks a count, and Log shows exactly those rows. That handoff is the whole
// reason the four audit tabs were folded into one, and it only works if the
// store behaves precisely — so the behaviours the UI leans on are pinned here.
//
// No jsdom needed: this is a plain zustand store with no browser dependency.

function reset() {
  useAuditFilterStore.getState().reset();
}

test("a fresh store carries the page limit and no active filter", () => {
  reset();
  const { filters } = useAuditFilterStore.getState();
  assert.equal(filters.limit, 100);
  assert.equal(countActiveAuditFilters(filters), 0);
});

test("patch merges rather than replaces", () => {
  reset();
  const { patch } = useAuditFilterStore.getState();
  patch({ target: "glb" });
  patch({ status: "error" });
  const { filters } = useAuditFilterStore.getState();
  assert.equal(filters.target, "glb", "second patch dropped the first");
  assert.equal(filters.status, "error");
  assert.equal(countActiveAuditFilters(filters), 2);
});

test("every mutation clears the page cursor", () => {
  // before_id is a keyset cursor into a result set. Carrying it across a
  // filter change asks the server to continue paging a list that no longer
  // exists, which silently returns the wrong page rather than erroring.
  reset();
  const s = () => useAuditFilterStore.getState();

  s().set({ limit: 100, before_id: 4321 });
  s().patch({ target: "glb" });
  assert.equal(s().filters.before_id, undefined, "patch kept a stale cursor");

  s().set({ limit: 100, before_id: 4321 });
  s().toggleStatus("error");
  assert.equal(s().filters.before_id, undefined, "toggleStatus kept a stale cursor");
});

test("toggleStatus selects, then clears on a second click", () => {
  // The Overview tiles are toggles: clicking Failed drills in, clicking it
  // again returns to the unfiltered view. Without the second half the operator
  // can strand themselves in a filtered state and read it as the whole truth.
  reset();
  const s = () => useAuditFilterStore.getState();

  s().toggleStatus("error");
  assert.equal(s().filters.status, "error");

  s().toggleStatus("error");
  assert.equal(s().filters.status, undefined, "second click did not clear");
});

test("toggleStatus switches directly between states", () => {
  reset();
  const s = () => useAuditFilterStore.getState();
  s().toggleStatus("error");
  s().toggleStatus("queued");
  assert.equal(s().filters.status, "queued");
});

test("toggleStatus leaves the other filters alone", () => {
  // Drilling in from Overview must narrow, not restart: whatever the operator
  // had already filtered to is the context they are drilling within.
  reset();
  const s = () => useAuditFilterStore.getState();
  s().patch({ target: "step", key: "beams" });
  s().toggleStatus("error");
  const { filters } = s();
  assert.equal(filters.target, "step");
  assert.equal(filters.key, "beams");
  assert.equal(filters.status, "error");
});

test("reset clears everything but keeps the page limit", () => {
  reset();
  const s = () => useAuditFilterStore.getState();
  s().patch({ target: "glb", status: "error", key: "x" });
  s().reset();
  assert.equal(countActiveAuditFilters(s().filters), 0);
  assert.equal(s().filters.limit, 100, "reset dropped the page size");
});

test("countActive ignores paging fields, counts only operator-set filters", () => {
  // The count drives the "Filters (n)" badge on the collapsed bar. Counting
  // limit/before_id would show a filter that the operator never set and cannot
  // clear — on a bar that is collapsed by default on mobile.
  assert.equal(countActiveAuditFilters({ limit: 100, before_id: 99 }), 0);
  assert.equal(countActiveAuditFilters({ limit: 100, status: "error" }), 1);
  // Empty string is what a cleared <select> yields; it must not count.
  assert.equal(countActiveAuditFilters({ limit: 100, target: "" }), 0);
});

test("every filter key the bar renders is counted", () => {
  // Guards the bar and the badge drifting apart: a control added to
  // AuditFilterBar without its key here would filter invisibly.
  const all = Object.fromEntries(AUDIT_FILTER_KEYS.map((k) => [k, "x"]));
  assert.equal(countActiveAuditFilters({ limit: 100, ...all }), AUDIT_FILTER_KEYS.length);
});
