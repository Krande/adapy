import assert from "node:assert/strict";
import { test } from "node:test";

import { filterUsers, userLabel, userScopes } from "@/components/admin/users/userScopes";
import type { AdminUser } from "@/services/api/adminUsers";

// The Users tab's scope list must match what the user's own scope picker
// (/api/me) offers: Personal and Shared for everyone, one entry per project
// membership, corpora for admins. A panel that disagreed with the picker would
// be answering "where can this person go?" wrongly in the one place built to
// answer it.

function user(over: Partial<AdminUser> = {}): AdminUser {
  return {
    sub: "sub-1",
    email: null,
    display_name: null,
    last_seen_at: null,
    created_at: null,
    is_admin: null,
    groups: null,
    first_activity_at: null,
    last_activity_at: null,
    projects: [],
    ...over,
  };
}

test("every account has Personal and Shared, keyed as the audit log keys them", () => {
  const scopes = userScopes(user({ sub: "abc" }));
  assert.deepEqual(
    scopes.map((s) => [s.kind, s.id, s.implicit]),
    [
      ["user", "abc", true],
      ["shared", null, true],
    ],
  );
});

test("project memberships follow, archived ones kept and flagged", () => {
  const scopes = userScopes(
    user({
      projects: [
        { id: "p1", slug: "live", name: "Live", role: "owner", added_at: null, archived: false },
        { id: "p2", slug: "old", name: "Old", role: "member", added_at: null, archived: true },
      ],
    }),
  );
  const projects = scopes.filter((s) => s.kind === "project");
  assert.deepEqual(
    projects.map((s) => [s.id, s.role, s.archived]),
    [
      ["p1", "owner", false],
      ["p2", "member", true],
    ],
  );
});

test("corpora only for a recorded admin; unknown is not admin", () => {
  assert.ok(userScopes(user({ is_admin: true })).some((s) => s.kind === "corpus"));
  assert.ok(!userScopes(user({ is_admin: false })).some((s) => s.kind === "corpus"));
  assert.ok(!userScopes(user({ is_admin: null })).some((s) => s.kind === "corpus"));
});

test("label falls back name → email → sub", () => {
  assert.equal(userLabel(user({ display_name: "Ada", email: "a@x" })), "Ada");
  assert.equal(userLabel(user({ email: "a@x" })), "a@x");
  assert.equal(userLabel(user({ sub: "ci:demo" })), "ci:demo");
});

test("search matches name, email and sub, case-insensitively", () => {
  const users = [
    user({ sub: "s1", display_name: "Ada Lovelace" }),
    user({ sub: "s2", email: "grace@navy.mil" }),
    user({ sub: "ci:demo:build" }),
  ];
  assert.deepEqual(filterUsers(users, "lovelace").map((u) => u.sub), ["s1"]);
  assert.deepEqual(filterUsers(users, "NAVY").map((u) => u.sub), ["s2"]);
  assert.deepEqual(filterUsers(users, "ci:demo").map((u) => u.sub), ["ci:demo:build"]);
  assert.equal(filterUsers(users, "  ").length, 3);
});
