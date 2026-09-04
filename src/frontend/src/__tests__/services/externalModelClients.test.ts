// The browser-side external-model provider registry.
//
// Imports the leaf module directly rather than `externalModels`, which reaches
// the worker and so cannot load outside a browser. The dispatch that consumes
// this registry is covered in externalModelDispatch.test.ts.

import { strict as assert } from "node:assert";
import { afterEach, test } from "node:test";

import {
  ExternalModelsError,
  externalModelClient,
  externalModelClientProviders,
  registerExternalModelClient,
  resetExternalModelClients,
  unregisterExternalModelClient,
  type ExternalModelClient,
} from "../../services/externalModelClients";

/** A read-only client. `modelUploadUrl` is absent on purpose — its absence is
 *  what declares the catalogue read-only. */
function client(): ExternalModelClient {
  return {
    listCollections: async () => [],
    listModels: async () => [],
    modelUrl: async () => ({ url: "https://example.invalid/one.glb" }),
  };
}

afterEach(() => {
  resetExternalModelClients();
});

test("a registered client is resolvable by id, and gone once unregistered", () => {
  const impl = client();
  registerExternalModelClient("vendor", impl);
  assert.equal(externalModelClient("vendor"), impl);

  unregisterExternalModelClient("vendor");
  assert.equal(externalModelClient("vendor"), null);
});

test("an unregistered id resolves to null, not undefined", () => {
  // The dispatch branches on `if (impl)`, but callers may compare explicitly.
  assert.equal(externalModelClient("nobody"), null);
});

test("registering twice replaces rather than duplicates", () => {
  // A plugin module evaluated twice must not appear twice in the picker.
  registerExternalModelClient("vendor", client());
  const second = client();
  registerExternalModelClient("vendor", second);

  assert.equal(externalModelClient("vendor"), second);
  assert.deepEqual(externalModelClientProviders(), [
    { id: "vendor", label: "vendor" },
  ]);
});

test("an empty provider id is refused rather than registered under ''", () => {
  assert.throws(() => registerExternalModelClient("  ", client()), ExternalModelsError);
  assert.throws(() => registerExternalModelClient("", client()), ExternalModelsError);
});

test("the id is trimmed on registration, lookup and removal", () => {
  const impl = client();
  registerExternalModelClient(" vendor ", impl);
  assert.equal(externalModelClient("vendor"), impl);
  assert.deepEqual(externalModelClientProviders(), [
    { id: "vendor", label: "vendor" },
  ]);

  unregisterExternalModelClient("  vendor  ");
  assert.equal(externalModelClient("vendor"), null);
});

test("label defaults to the id, and an explicit one is kept", () => {
  registerExternalModelClient("bare", client());
  registerExternalModelClient("named", client(), { label: "Named Vendor" });
  assert.deepEqual(externalModelClientProviders(), [
    { id: "bare", label: "bare" },
    { id: "named", label: "Named Vendor" },
  ]);
});

test("providers are listed in registration order", () => {
  // The picker renders this list as-is, so the order is observable.
  registerExternalModelClient("first", client());
  registerExternalModelClient("second", client());
  registerExternalModelClient("third", client());
  assert.deepEqual(
    externalModelClientProviders().map((p) => p.id),
    ["first", "second", "third"],
  );
});

test("re-registering keeps the original position", () => {
  // Map.set on an existing key does not move it, and the picker should not
  // reshuffle because a plugin re-registered.
  registerExternalModelClient("first", client());
  registerExternalModelClient("second", client());
  registerExternalModelClient("first", client(), { label: "First" });
  assert.deepEqual(externalModelClientProviders(), [
    { id: "first", label: "First" },
    { id: "second", label: "second" },
  ]);
});

test("unregistering an id that was never registered is a no-op", () => {
  registerExternalModelClient("vendor", client());
  unregisterExternalModelClient("nobody");
  assert.deepEqual(externalModelClientProviders(), [
    { id: "vendor", label: "vendor" },
  ]);
});

// --- revisions: presence is the declaration ---------------------------------

test("a client without listModelRevisions is a single-version provider", () => {
  // Absence is the declaration, exactly as with modelUploadUrl. A consumer
  // checks for the method rather than for a capability flag.
  const impl = client();
  registerExternalModelClient("vendor", impl);
  assert.equal(typeof externalModelClient("vendor")?.listModelRevisions, "undefined");
});

test("a versioned client exposes its revisions and honours one on modelUrl", async () => {
  const impl: ExternalModelClient = {
    listCollections: async () => [],
    listModels: async () => [],
    listModelRevisions: async (_collection, modelId) =>
      modelId === "versioned"
        ? [
            { id: "b", name: "b", createdAt: "2024-01-02T00:00:00Z", current: true },
            { id: "a", name: "a", createdAt: "2024-01-01T00:00:00Z" },
          ]
        : [],
    modelUrl: async (_collection, modelId, opts) => ({
      url: opts?.revision
        ? `https://example.invalid/${modelId}@${opts.revision}.glb`
        : `https://example.invalid/${modelId}.glb`,
    }),
  };
  registerExternalModelClient("vendor", impl);
  const got = externalModelClient("vendor");

  const revisions = (await got?.listModelRevisions?.("c", "versioned")) ?? [];
  assert.equal(revisions.length, 2);
  assert.equal(revisions[0].current, true);

  // An unversioned model in a versioned provider is empty, not an error, so a
  // caller needs no branch between the two cases.
  assert.deepEqual(await got?.listModelRevisions?.("c", "plain"), []);

  const pinned = await got?.modelUrl("c", "versioned", { revision: "a" });
  assert.equal(pinned?.url, "https://example.invalid/versioned@a.glb");
  const current = await got?.modelUrl("c", "versioned");
  assert.equal(current?.url, "https://example.invalid/versioned.glb");
});
