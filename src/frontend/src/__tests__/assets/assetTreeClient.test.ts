// `registerAssetTreeClient` (plugin API 1.6.0) and the per-call dispatch behind it.
//
// The registry exists for one case: a provider that must be read AS THE SIGNED-IN USER, which a
// worker cannot proxy without either taking the person's identity or flattening every user to one
// service account. What these pin is the property that makes it safe to add — that a reading path
// cannot tell which half answered, so the two halves cannot drift apart by being called
// differently.

import assert from "node:assert/strict";
import { test } from "node:test";

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
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const {
  AssetTreeClientError,
  assetTreeClient,
  fetchAssetDelivery,
  fetchAssetHierarchy,
  registerAssetTreeClient,
  registeredAssetTreeClients,
  unregisterAssetTreeClient,
} = await import("@/services/assets");

const SCOPE = "user:me" as never;

function slice(provider: string) {
  return {
    schema: "ada.assets/hierarchy@1" as const,
    provider,
    collection: "c",
    root: null,
    producedAt: "2026-09-25T00:00:00Z",
    depth: 1,
    nodes: [],
  };
}

function fakeClient(provider: string, calls: string[]) {
  return {
    collections: async () => {
      calls.push("collections");
      return [{ collection: "c" }];
    },
    hierarchy: async (_s: never, collection: string, opts: { root?: string; depth: number }) => {
      calls.push(`hierarchy:${collection}:${opts.root ?? "-"}:${opts.depth}`);
      return slice(provider);
    },
    delivery: async (_s: never, _c: string, node: string) => {
      calls.push(`delivery:${node}`);
      return null;
    },
  };
}

test("a provider with no registration is served by the route, not by a guess", () => {
  assert.equal(assetTreeClient("nobody-registered-this"), null);
});

test("an id must actually name a provider", () => {
  // The id IS the provider id the index reports. An empty one would register a client that can
  // never be dispatched to, and would look installed.
  assert.throws(() => registerAssetTreeClient("", fakeClient("x", [])), AssetTreeClientError);
  assert.throws(() => registerAssetTreeClient("  ", fakeClient("x", [])), AssetTreeClientError);
  assert.throws(
    () => registerAssetTreeClient("p", null as never),
    AssetTreeClientError,
  );
});

test("a registered client answers the hierarchy, and the caller cannot tell", async () => {
  const calls: string[] = [];
  registerAssetTreeClient("live", fakeClient("live", calls), { label: "Live" });
  try {
    const got = await fetchAssetHierarchy(SCOPE, "live", "c", { root: "r1", revision: "2026-09-25", depth: 3 });
    assert.equal(got.provider, "live");
    // The REVISION is not forwarded: a live provider reads a system of record that has no
    // revisions to ask about -- that is what makes it live. Depth is.
    assert.deepEqual(calls, ["hierarchy:c:r1:3"]);
  } finally {
    unregisterAssetTreeClient("live");
  }
});

test("`null` from a live client is an answer, not a failure", async () => {
  // "this node has nothing to deliver" is a real reply from a live provider; the route has no
  // such case (it 404s). Turning it into an error here would make the two halves behave
  // differently for the same question.
  const calls: string[] = [];
  registerAssetTreeClient("live", fakeClient("live", calls), {});
  try {
    assert.equal(await fetchAssetDelivery(SCOPE, "live", "c", "n1"), null);
    assert.deepEqual(calls, ["delivery:n1"]);
  } finally {
    unregisterAssetTreeClient("live");
  }
});

test("re-registering replaces rather than accumulating", async () => {
  const first: string[] = [];
  const second: string[] = [];
  registerAssetTreeClient("live", fakeClient("live", first));
  registerAssetTreeClient("live", fakeClient("live", second));
  try {
    await fetchAssetHierarchy(SCOPE, "live", "c", { revision: "r" });
    assert.deepEqual(first, [], "the replaced client must not still be called");
    assert.equal(second.length, 1);
  } finally {
    unregisterAssetTreeClient("live");
  }
});

test("unregistering returns the provider to the route", () => {
  registerAssetTreeClient("live", fakeClient("live", []));
  assert.notEqual(assetTreeClient("live"), null);
  unregisterAssetTreeClient("live");
  assert.equal(assetTreeClient("live"), null);
});

test("the registry can be listed, for a panel that says what this deployment reaches", () => {
  registerAssetTreeClient("zeta", fakeClient("zeta", []), { label: "Zeta" });
  registerAssetTreeClient("alpha", fakeClient("alpha", []));
  try {
    assert.deepEqual(registeredAssetTreeClients(), [
      { id: "alpha", label: "alpha" }, // label defaults to the id
      { id: "zeta", label: "Zeta" },
    ]);
  } finally {
    unregisterAssetTreeClient("zeta");
    unregisterAssetTreeClient("alpha");
  }
});

test("the plugin API advertises 1.6.0", async () => {
  // A plugin built against `registerAssetTreeClient` and loaded into a core that predates it
  // registers into nothing and its collections silently never appear -- the mismatch a minor
  // bump exists to turn into one log line.
  const { PLUGIN_API_VERSION } = await import("@/plugins/registry");
  assert.equal(PLUGIN_API_VERSION, "1.6.0");
});
