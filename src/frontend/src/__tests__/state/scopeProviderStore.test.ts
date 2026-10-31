import { strict as assert } from "node:assert";
import { test } from "node:test";

// `scopeStore` persists to sessionStorage; a bare node process has none, and nothing here reads it.
const noop = () => undefined;
(globalThis as unknown as { sessionStorage: unknown }).sessionStorage = { getItem: () => null, setItem: noop, removeItem: noop };

const { scopeFromUrlPart, scopeUrlPart, withProviderStore } = await import("../../state/scopeStore");

const mine = [
  { kind: "user" as const, id: "me", name: "My files" },
  { kind: "shared" as const, id: null, name: "Shared" },
];

test("an admin gets the provider store last; a member never does", () => {
  const admin = withProviderStore(mine, true);
  assert.deepEqual(admin.map(scopeUrlPart), ["user:me", "shared", "providers"]);
  assert.equal(admin[2].name, "Provider store");
  // Even if a server listed it for someone who is not an admin.
  const listed = [...mine, { kind: "providers" as const, id: null, name: "Provider store" }];
  assert.deepEqual(withProviderStore(listed, false).map(scopeUrlPart), ["user:me", "shared"]);
});

test("a store the server already lists is not added twice", () => {
  const listed = [...mine, { kind: "providers" as const, id: null, name: "Provider store" }];
  assert.equal(withProviderStore(listed, true).length, 3);
});

test("the provider store round-trips through its URL part", () => {
  const s = scopeFromUrlPart("providers");
  assert.equal(s.kind, "providers");
  assert.equal(scopeUrlPart(s), "providers");
});
