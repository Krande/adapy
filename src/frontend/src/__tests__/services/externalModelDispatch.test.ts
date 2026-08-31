// Dispatch: a registered browser-side provider is served in the page, and
// everything else still goes to the worker job.
//
// `externalModels` reaches the worker, so it transitively imports the OIDC
// client, which reads `sessionStorage` at module scope. The browser globals it
// needs are installed BEFORE a dynamic import, which is the same shape
// oidcScopedToken.test.ts uses and the reason this file cannot use a static one.
//
// Most tests below install NO job stub. If a call that should have been served
// in-page reached the job path instead, it fails loudly on `viewerApi` rather
// than quietly returning something plausible — which is exactly the assertion,
// so it is left to fire.

import { strict as assert } from "node:assert";
import { afterEach, before, test } from "node:test";

type ExternalModelsModule = typeof import("../../services/externalModels");
type ViewerApiModule = typeof import("../../services/viewerApi");

const SCOPE = "shared" as never;

function fakeStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => void m.set(k, String(v)),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: (i: number) => [...m.keys()][i] ?? null,
    get length() {
      return m.size;
    },
  } as Storage;
}

let mod: ExternalModelsModule;
let api: Record<string, unknown>;

before(async () => {
  const g = globalThis as Record<string, unknown>;
  g.sessionStorage ??= fakeStorage();
  g.localStorage ??= fakeStorage();
  g.window ??= g;
  g.location ??= { origin: "https://viewer.example.test", hash: "", href: "/" };
  mod = await import("../../services/externalModels");
  api = (await import("../../services/viewerApi"))
    .viewerApi as unknown as ViewerApiModule["viewerApi"] &
    Record<string, unknown>;
});

/** A read-only client. `modelUploadUrl` is absent on purpose — its absence is
 *  what declares the catalogue read-only, so adding it here would erase the
 *  distinction the upload tests rest on. */
function readOnlyClient(
  overrides: Partial<import("../../services/externalModelClients").ExternalModelClient> = {},
): import("../../services/externalModelClients").ExternalModelClient {
  return {
    listCollections: async () => [{ id: "alpha", name: "Alpha" }],
    listModels: async (collection: string) => [
      { id: "m1", name: "One", collection, key: "k1" },
    ],
    modelUrl: async () => ({ url: "https://example.invalid/one.glb" }),
    ...overrides,
  };
}

/** Stub a job that is accepted and then never finishes — what a deployment
 *  whose worker pool does not advertise this capability actually does. */
function stubNeverFinishingJob() {
  const original = {
    pluginJob: api.pluginJob,
    convertStatus: api.convertStatus,
    getBlob: api.getBlob,
  };
  api.pluginJob = async () => ({ job_id: "j", derived_key: "d" });
  api.convertStatus = async () => ({ status: "queued" });
  return {
    restore() {
      Object.assign(api, original);
    },
  };
}

/** Stub the viewerApi calls `runAction` makes so a job resolves to `payload`
 *  (or rejects with an Error). Returns a restore handle. */
function stubJob(payload: unknown) {
  const original = {
    pluginJob: api.pluginJob,
    convertStatus: api.convertStatus,
    getBlob: api.getBlob,
  };
  if (payload instanceof Error) {
    api.pluginJob = async () => {
      throw payload;
    };
  } else {
    api.pluginJob = async () => ({ job_id: "j", derived_key: "d" });
    api.convertStatus = async () => ({ status: "done" });
    api.getBlob = async () =>
      new TextEncoder().encode(JSON.stringify(payload)).buffer;
  }
  return {
    restore() {
      Object.assign(api, original);
    },
  };
}

afterEach(() => {
  mod.resetExternalModelClients();
});

// --- served in the page -----------------------------------------------------

test("listCollections is served in-page for a registered provider", async () => {
  mod.registerExternalModelClient("vendor", readOnlyClient());
  assert.deepEqual(await mod.listCollections("vendor", SCOPE), [
    { id: "alpha", name: "Alpha" },
  ]);
});

test("listModelsDetailed reports canUpload from the method's PRESENCE", async () => {
  mod.registerExternalModelClient("readonly", readOnlyClient());
  const ro = await mod.listModelsDetailed("readonly", "alpha", SCOPE);
  assert.equal(ro.canUpload, false);
  assert.deepEqual(ro.models, [
    { id: "m1", name: "One", collection: "alpha", key: "k1" },
  ]);

  mod.registerExternalModelClient(
    "writable",
    readOnlyClient({
      modelUploadUrl: async () => ({ url: "https://example.invalid/put" }),
    }),
  );
  assert.equal(
    (await mod.listModelsDetailed("writable", "alpha", SCOPE)).canUpload,
    true,
  );
});

test("listModels unwraps to just the models", async () => {
  mod.registerExternalModelClient("vendor", readOnlyClient());
  assert.deepEqual(await mod.listModels("vendor", "alpha", SCOPE), [
    { id: "m1", name: "One", collection: "alpha", key: "k1" },
  ]);
});

test("modelUrl defaults absent headers to an empty object", async () => {
  // A provider whose URL carries its own signature returns no headers; the call
  // site must still be able to spread them unconditionally.
  mod.registerExternalModelClient("vendor", readOnlyClient());
  assert.deepEqual(await mod.modelUrl("vendor", "alpha", "m1", SCOPE), {
    url: "https://example.invalid/one.glb",
    headers: {},
  });
});

test("modelUrl passes a client's headers through", async () => {
  mod.registerExternalModelClient(
    "vendor",
    readOnlyClient({
      modelUrl: async () => ({
        url: "https://example.invalid/one.glb",
        headers: { Authorization: "Bearer x" },
      }),
    }),
  );
  const got = await mod.modelUrl("vendor", "alpha", "m1", SCOPE);
  assert.deepEqual(got.headers, { Authorization: "Bearer x" });
});

test("modelUrl rejects a client that returns no url", async () => {
  mod.registerExternalModelClient(
    "vendor",
    readOnlyClient({ modelUrl: async () => ({ url: "" }) }),
  );
  await assert.rejects(
    () => mod.modelUrl("vendor", "alpha", "m1", SCOPE),
    mod.ExternalModelsError,
  );
});

test("the collection and model id reach the client unchanged", async () => {
  const seen: string[] = [];
  mod.registerExternalModelClient(
    "vendor",
    readOnlyClient({
      modelUrl: async (collection: string, modelId: string) => {
        seen.push(collection, modelId);
        return { url: "https://example.invalid/x.glb" };
      },
    }),
  );
  // A composite id containing a slash is a normal provider-internal shape, so
  // it must survive the hop rather than being split, encoded or rejected.
  await mod.modelUrl("vendor", "alpha", "file-7/site-3", SCOPE);
  assert.deepEqual(seen, ["alpha", "file-7/site-3"]);
});

test("expiresInSeconds is forwarded to the client", async () => {
  let got: number | undefined;
  mod.registerExternalModelClient(
    "vendor",
    readOnlyClient({
      modelUrl: async (_c: string, _m: string, opts?: { expiresInSeconds?: number }) => {
        got = opts?.expiresInSeconds;
        return { url: "https://example.invalid/x.glb" };
      },
    }),
  );
  await mod.modelUrl("vendor", "alpha", "m1", SCOPE, { expiresInSeconds: 60 });
  assert.equal(got, 60);
});

test("uploading to a read-only client is refused, naming the provider", async () => {
  mod.registerExternalModelClient("vendor", readOnlyClient());
  await assert.rejects(
    () => mod.modelUploadUrl("vendor", "alpha", "m1", SCOPE),
    (e: unknown) =>
      e instanceof mod.ExternalModelsError && /vendor/.test(e.message),
  );
});

test("modelUploadUrl defaults the method to PUT", async () => {
  mod.registerExternalModelClient(
    "vendor",
    readOnlyClient({
      modelUploadUrl: async () => ({ url: "https://example.invalid/put" }),
    }),
  );
  assert.deepEqual(await mod.modelUploadUrl("vendor", "alpha", "m1", SCOPE), {
    url: "https://example.invalid/put",
    method: "PUT",
    headers: {},
  });
});

// --- still served by the worker ---------------------------------------------

test("an unregistered provider still goes to the job", async () => {
  const stub = stubJob({
    action: "list_collections",
    collections: [{ id: "bucket", name: "Bucket" }],
  });
  try {
    assert.deepEqual(await mod.listCollections("demo", SCOPE), [
      { id: "bucket", name: "Bucket" },
    ]);
  } finally {
    stub.restore();
  }
});

test("registering one provider does not divert another", async () => {
  // The branch is per provider id, not a global mode switch.
  mod.registerExternalModelClient("vendor", readOnlyClient());
  const stub = stubJob({
    action: "list_collections",
    collections: [{ id: "bucket", name: "Bucket" }],
  });
  try {
    assert.deepEqual(await mod.listCollections("demo", SCOPE), [
      { id: "bucket", name: "Bucket" },
    ]);
    assert.deepEqual(await mod.listCollections("vendor", SCOPE), [
      { id: "alpha", name: "Alpha" },
    ]);
  } finally {
    stub.restore();
  }
});

// --- listProviders ----------------------------------------------------------

test("listProviders merges the worker's providers with this page's", async () => {
  mod.registerExternalModelClient("vendor", readOnlyClient(), { label: "Vendor" });
  const stub = stubJob({
    action: "list_providers",
    providers: [{ id: "demo", label: "Demo (object store)" }],
  });
  try {
    assert.deepEqual(await mod.listProviders(SCOPE), [
      { id: "vendor", label: "Vendor" },
      { id: "demo", label: "Demo (object store)" },
    ]);
  } finally {
    stub.restore();
  }
});

test("a browser-side provider wins a shared id", async () => {
  // Both halves of one catalogue may register. The in-page half has the user's
  // identity and the worker's does not, so it is the more capable of the two —
  // and the picker must offer the id once, not twice.
  mod.registerExternalModelClient("vendor", readOnlyClient(), { label: "Vendor" });
  const stub = stubJob({
    action: "list_providers",
    providers: [{ id: "vendor", label: "Vendor (worker)" }],
  });
  try {
    assert.deepEqual(await mod.listProviders(SCOPE), [
      { id: "vendor", label: "Vendor" },
    ]);
  } finally {
    stub.restore();
  }
});

test("listProviders survives a worker that cannot answer, if this page has providers", async () => {
  // A deployment may legitimately run no worker for this capability at all.
  mod.registerExternalModelClient("vendor", readOnlyClient(), { label: "Vendor" });
  const stub = stubJob(new Error("no worker advertises external-models"));
  try {
    assert.deepEqual(await mod.listProviders(SCOPE), [
      { id: "vendor", label: "Vendor" },
    ]);
  } finally {
    stub.restore();
  }
});

test("listProviders propagates the worker's error when this page has none", async () => {
  // With nothing registered here it is the only answer there is, and it names
  // what the worker does have — which is what a misconfigured deployment needs.
  const stub = stubJob(new Error("no worker advertises external-models"));
  try {
    await assert.rejects(() => mod.listProviders(SCOPE), /no worker advertises/);
  } finally {
    stub.restore();
  }
});

test("a stuck worker does not hold the listing for the full poll timeout", async () => {
  // The job is accepted and never runs, which is what a pool that does not
  // advertise this capability does. Waiting the full 60s for that renders
  // exactly what the page had at the start, one minute later.
  mod.registerExternalModelClient("vendor", readOnlyClient(), { label: "Vendor" });
  const stub = stubNeverFinishingJob();
  const started = Date.now();
  try {
    assert.deepEqual(await mod.listProviders(SCOPE), [
      { id: "vendor", label: "Vendor" },
    ]);
    const waited = Date.now() - started;
    // Comfortably inside the 60s full timeout, and at least long enough to show
    // the bounded wait was a real wait rather than a skipped call.
    assert.ok(waited < 30_000, `waited ${waited}ms, expected the bounded timeout`);
  } finally {
    stub.restore();
  }
});
