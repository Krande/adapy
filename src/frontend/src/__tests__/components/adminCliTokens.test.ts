import assert from "node:assert/strict";
import { test } from "node:test";

import {
  filterTokens,
  isCiBot,
  tokenHint,
  tokenOwner,
  tokenStatus,
  untrackedValidUntil,
} from "@/components/admin/tokens/cliTokens";
import type { CliTokenRecord } from "@/services/api/adminProjects";

// The CLI Tokens tab is how an operator answers "which tokens are out there,
// and is THIS one still live?". The rules below are the ones that answer
// wrongly if they drift: a revoked token shown as merely expired, or a pasted
// token tail that finds nothing.

const NOW = Date.parse("2026-10-09T12:00:00Z");

function token(over: Partial<CliTokenRecord> = {}): CliTokenRecord {
  return {
    jti: "j1",
    sub: "alice-sub",
    email: "alice@example.invalid",
    display_name: "Alice",
    is_admin: false,
    label: "laptop",
    hint: "Xy3kQ9aB",
    issued_by: "alice-sub",
    issued_at: "2026-10-01T12:00:00Z",
    expires_at: "2026-10-31T12:00:00Z",
    last_used_at: null,
    revoked_at: null,
    revoked_by: null,
    ...over,
  };
}

test("status: active until expiry, expired after", () => {
  assert.equal(tokenStatus(token(), NOW), "active");
  assert.equal(tokenStatus(token({ expires_at: "2026-10-09T11:59:59Z" }), NOW), "expired");
});

test("status: revoked wins over expired", () => {
  const t = token({ revoked_at: "2026-10-02T00:00:00Z", expires_at: "2026-10-03T00:00:00Z" });
  assert.equal(tokenStatus(t, NOW), "revoked");
});

test("hint shows the tail, since every token starts with the same header", () => {
  assert.equal(tokenHint(token()), "eyJ…Xy3kQ9aB");
});

test("owner falls back from name to email to sub", () => {
  assert.equal(tokenOwner(token()), "Alice");
  assert.equal(tokenOwner(token({ display_name: null })), "alice@example.invalid");
  assert.equal(tokenOwner(token({ display_name: null, email: null })), "alice-sub");
});

test("CI bots are recognised by their subject", () => {
  assert.equal(isCiBot(token({ sub: "ci:demo:ada-build" })), true);
  assert.equal(isCiBot(token()), false);
});

test("search matches owner, label and the token tail", () => {
  const rows = [token(), token({ jti: "j2", sub: "ci:demo", display_name: "CI demo", label: null, hint: "Zz99Zz99" })];
  assert.deepEqual(filterTokens(rows, "laptop").map((t) => t.jti), ["j1"]);
  assert.deepEqual(filterTokens(rows, "ci:demo").map((t) => t.jti), ["j2"]);
  assert.deepEqual(filterTokens(rows, "zz99").map((t) => t.jti), ["j2"]);
  assert.deepEqual(filterTokens(rows, "  ").map((t) => t.jti), ["j1", "j2"]);
});

test("search finds a token from the whole pasted string", () => {
  const rows = [token(), token({ jti: "j2", hint: "Zz99Zz99" })];
  // Shaped like a token (three dot-separated parts), not a real one.
  const pasted = "header.payload.abcdefXy3kQ9aB";
  assert.deepEqual(filterTokens(rows, pasted).map((t) => t.jti), ["j1"]);
});

test("untracked tokens can live until tracking start + 30 days", () => {
  assert.equal(untrackedValidUntil("2026-10-09T00:00:00Z")?.toISOString(), "2026-11-08T00:00:00.000Z");
  assert.equal(untrackedValidUntil(null), null);
  assert.equal(untrackedValidUntil("not a date"), null);
});
