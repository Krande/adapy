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

const { assetSchedulesApi } = await import("@/services/api/assetSchedules");

const calls: { url: string; init?: RequestInit }[] = [];
globals.fetch = async (url: string, init?: RequestInit) => {
  calls.push({ url, init });
  return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
};

test("resetting a cursor is a DELETE naming the provider and collection", async () => {
  calls.length = 0;
  await assetSchedulesApi.adminResetAssetApplyCursor("vendor", "ALPHA");
  assert.match(calls[0].url, /\/admin\/asset-apply-cursors\?provider=vendor&collection=ALPHA$/);
  assert.equal(calls[0].init?.method, "DELETE");
});

test("a collection with characters a URL needs is encoded", async () => {
  calls.length = 0;
  await assetSchedulesApi.adminResetAssetApplyCursor("vendor", "A B/1");
  assert.match(calls[0].url, /collection=A\+B%2F1$/);
});