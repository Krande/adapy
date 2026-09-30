import assert from "node:assert/strict";
import { test } from "node:test";

import { auditHash, parseAdminHash, parseTabId } from "@/components/admin/adminTabs";

// Deep links into the admin panel after folding nine top-level tabs into three.
//
// These hashes live in bookmarks, in browser history and in in-app triggers
// (the conversion toast opens "audit_runs"). A link that silently lands on the
// WRONG panel is worse than one that 404s, because nothing tells the operator
// it happened — so every retired id is pinned here.

const NO_PLUGINS: ReadonlySet<string> = new Set();
const parse = (raw: string) => parseTabId(raw, NO_PLUGINS);

test("retired audit hashes resolve to their sub-tab", () => {
  assert.deepEqual(parse("audit_runs"), { tab: "audit", sub: "runs" });
  assert.deepEqual(parse("corpus"), { tab: "audit", sub: "corpora" });
  assert.deepEqual(parse("schedules"), { tab: "audit", sub: "schedules" });
});

test("retired performance hash resolves to its sub-tab", () => {
  assert.deepEqual(parse("frontend_loads"), { tab: "performance", sub: "frontend" });
});

test("retired procedural hashes resolve to their sub-tabs", () => {
  assert.deepEqual(parse("engines"), { tab: "procedural", sub: "engines" });
  assert.deepEqual(parse("system"), { tab: "procedural", sub: "systems" });
  assert.deepEqual(parse("equipment"), { tab: "procedural", sub: "equipment" });
});

test("the new nested form works for every grouped tab", () => {
  assert.deepEqual(parse("audit/log"), { tab: "audit", sub: "log" });
  assert.deepEqual(parse("performance/workers"), { tab: "performance", sub: "workers" });
  assert.deepEqual(parse("procedural/systems"), { tab: "procedural", sub: "systems" });
});

test("a bare grouped tab lands on its default sub-tab", () => {
  // sub undefined means "let the tab choose", not "unknown tab".
  assert.deepEqual(parse("audit"), { tab: "audit", sub: undefined });
  assert.deepEqual(parse("performance"), { tab: "performance", sub: undefined });
});

test("an unknown sub-tab falls back to the tab's default rather than the wrong panel", () => {
  assert.deepEqual(parse("performance/nonsense"), { tab: "performance", sub: undefined });
  assert.deepEqual(parse("procedural/nonsense"), { tab: "procedural", sub: undefined });
});

test("ungrouped tabs still resolve unchanged", () => {
  for (const id of ["issues", "projects", "users", "external_models", "storage", "workers", "conversion"]) {
    assert.deepEqual(parse(id), { tab: id });
  }
});

test("a plugin id survives, and is never split on its colon", () => {
  const plugins: ReadonlySet<string> = new Set(["capacity-manager:panel"]);
  assert.deepEqual(parseTabId("capacity-manager:panel", plugins), { tab: "capacity-manager:panel" });
});

test("an unknown hash falls back to audit", () => {
  assert.deepEqual(parse("does-not-exist"), { tab: "audit" });
  assert.deepEqual(parse(""), { tab: "audit" });
});

// The Users tab's "Audit log" link: /admin#audit/log?user_sub=<sub>.

test("the audit user filter round-trips through the hash, whatever the sub contains", () => {
  for (const sub of ["abc-123", "ci:demo:build", "a/b?c#d&e=f", "Ada Lovelace"]) {
    const hash = auditHash(sub, "log");
    assert.deepEqual(parseAdminHash(hash, NO_PLUGINS), { tab: "audit", sub: "log", userSub: sub });
  }
});

test("a bare audit hash carries no user, and the query never leaks into the tab id", () => {
  assert.equal(auditHash(), "audit");
  assert.equal(auditHash(undefined, "runs"), "audit/runs");
  assert.deepEqual(parseAdminHash("audit", NO_PLUGINS), { tab: "audit", sub: undefined });
  assert.deepEqual(parseAdminHash("audit?user_sub=x", NO_PLUGINS), {
    tab: "audit",
    sub: undefined,
    userSub: "x",
  });
  // Empty value is no filter, not a filter on "".
  assert.deepEqual(parseAdminHash("audit/log?user_sub=", NO_PLUGINS), { tab: "audit", sub: "log" });
});

test("a user filter on a non-audit tab is ignored rather than applied elsewhere", () => {
  assert.deepEqual(parseAdminHash("projects?user_sub=x", NO_PLUGINS), { tab: "projects" });
  // Legacy ids keep resolving with a query attached.
  assert.deepEqual(parseAdminHash("audit_runs?user_sub=x", NO_PLUGINS), {
    tab: "audit",
    sub: "runs",
    userSub: "x",
  });
});
