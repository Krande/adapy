import assert from "node:assert/strict";
import { test } from "node:test";

// `authedFetch` reaches `services/auth/oidc.ts`, which reads `sessionStorage` at module load, so
// the browser globals are stubbed BEFORE the dynamic import (as viewerApiMethodSet.test.ts does).
const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown; fetch: unknown };
globals.sessionStorage = storage;
(globalThis as unknown as { window: unknown }).window = { location: { origin: "https://app.example.test", pathname: "/", search: "" } };
globals.localStorage = storage;

const { providerGrantsApi } = await import("@/services/api/providerGrants");

const calls: { url: string; init?: RequestInit }[] = [];
globals.fetch = async (url: string, init?: RequestInit) => {
  calls.push({ url, init });
  return new Response(JSON.stringify({ grants: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
};

test("the admin list narrows by what is given", async () => {
  calls.length = 0;
  await providerGrantsApi.adminProviderGrants({ target_scope: "projects/p1", provider: "vendor" });
  assert.match(calls[0].url, /\/admin\/provider-grants\?target_scope=projects%2Fp1&provider=vendor$/);
});

test("set is a PUT and revoke a DELETE, each with a JSON body", async () => {
  calls.length = 0;
  await providerGrantsApi.adminSetProviderGrant({
    target_scope: "shared",
    provider: "vendor",
    collection: null,
    attributes: true,
    geometry: true,
    request: false,
  });
  await providerGrantsApi.adminRevokeProviderGrant({ target_scope: "shared", provider: "vendor", collection: null });
  assert.equal(calls[0].init?.method, "PUT");
  assert.equal(JSON.parse(String(calls[0].init?.body)).request, false);
  assert.equal(calls[1].init?.method, "DELETE");
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { target_scope: "shared", provider: "vendor", collection: null });
});

test("the checkboxes are one POST of what to grant and what to revoke", async () => {
  calls.length = 0;
  await providerGrantsApi.adminSetProviderGrantCollections({
    target_scope: "projects/p1",
    provider: "vendor",
    grant: ["ALPHA"],
    revoke: ["beta"],
  });
  assert.match(calls[0].url, /\/admin\/provider-grants\/collections$/);
  assert.equal(calls[0].init?.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    target_scope: "projects/p1",
    provider: "vendor",
    grant: ["ALPHA"],
    revoke: ["beta"],
  });
});

test("a scope's own grants are read from the scope route", async () => {
  calls.length = 0;
  await providerGrantsApi.scopeProviderGrants("project:p1");
  assert.match(calls[0].url, /\/scopes\/project%3Ap1\/provider-grants$/);
});
