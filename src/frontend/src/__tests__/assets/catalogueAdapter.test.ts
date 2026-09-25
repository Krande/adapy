// An `ExternalModelClient` seen through the asset tree's fetching abstraction.
//
// The adapter exists so one provider does not describe the same tree twice: the mesh catalogue
// already implements `ExternalModelClient` (it must, to authenticate as the signed-in user), and
// core translates rather than asking the plugin for a second description.
//
// What these pin is that core asserts NOTHING about how the provider stores its tree. `listModels`
// is a list of ROOTS; whether a root has anything under it is the provider's question to answer,
// and this client simply has no call that does. A catalogue and a deep CSG hierarchy are read
// through the same two calls and differ only in what the delivery claim says.

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

const { assetTreeClientFromCatalogue } = await import("@/services/assetCatalogueAdapter");

const SCOPE = "user:me" as never;

function catalogue(calls: string[] = []) {
  return {
    listCollections: async () => {
      calls.push("listCollections");
      return [{ id: "plant-a", name: "Plant A" }];
    },
    listModels: async (collection: string) => {
      calls.push(`listModels:${collection}`);
      return [
        { id: "m1", name: "Deck", collection, key: "k1" },
        { id: "m2", name: "", collection, key: "k2" },
      ];
    },
    modelUrl: async (collection: string, modelId: string, opts?: { revision?: string }) => {
      calls.push(`modelUrl:${collection}/${modelId}@${opts?.revision ?? "-"}`);
      return { url: `https://cat.invalid/${modelId}.glb`, headers: { Authorization: "Bearer x" } };
    },
  };
}

test("collections come through with their labels", async () => {
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  assert.deepEqual(await c.collections(SCOPE), [{ collection: "plant-a", label: "Plant A" }]);
});

test("listModels are ROOTS of the collection's tree, not children of an invented parent", async () => {
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  const slice = await c.hierarchy(SCOPE, "plant-a", { depth: 5 });

  assert.equal(slice.schema, "ada.assets/hierarchy@1");
  assert.equal(slice.provider, "mesh-catalogue");
  assert.equal(slice.nodes.length, 2);
  assert.deepEqual(
    slice.nodes.map((n) => [n.id, n.parent, n.label, n.leaf, n.delivery]),
    [
      // `parent: null` -- a top-level node. The collection is the thing being listed, not a node
      // in the tree, and synthesising one would be core inventing structure the provider never
      // reported.
      ["plant-a/m1", null, "Deck", true, "mesh"],
      // An unnamed model falls back to its id rather than rendering as a blank row.
      ["plant-a/m2", null, "m2", true, "mesh"],
    ],
  );
  // `depth` reports what was FETCHED, not what was asked for: this client returns the roots in
  // one call and has no second level to descend into, so a depth of 5 cannot be honoured and is
  // not claimed to have been.
  assert.equal(slice.depth, 1);
  assert.ok(slice.nodes.every((n) => n.provider === "mesh-catalogue"));
});

test("a rooted request this client cannot answer is an empty subtree, not an error", async () => {
  // `leaf: true` above says THIS CLIENT offers no deeper fetch -- it is not a claim that the
  // storage format is shallow. A consumer walking a mixed tree cannot know in advance which
  // providers are deep, and a throw here would make this one break the walk.
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  const slice = await c.hierarchy(SCOPE, "plant-a", { root: "plant-a/m1", depth: 1 });
  assert.deepEqual(slice.nodes, []);
  assert.equal(slice.root, "plant-a/m1");
});

test("a stored model delivers a MESH claim", async () => {
  const calls: string[] = [];
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue(calls));
  const claim = await c.delivery(SCOPE, "plant-a", "plant-a/m1");

  assert.equal(claim?.kind, "mesh", "a catalogue stores built models; a CSG provider would say `build`");
  assert.equal((claim as { url: string }).url, "https://cat.invalid/m1.glb");
  assert.deepEqual((claim as { headers?: Record<string, string> }).headers, { Authorization: "Bearer x" });
  assert.equal(claim?.provider, "mesh-catalogue");
  assert.deepEqual(calls, ["modelUrl:plant-a/m1@-"]);
});

test("an unversioned catalogue reports `current`, not an invented revision", async () => {
  // Minting an id here would make an unversioned catalogue look versioned, and the revision
  // picker would offer history that does not exist.
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  const claim = await c.delivery(SCOPE, "plant-a", "plant-a/m1");
  assert.equal(claim?.revision, "current");
});

test("a requested revision is forwarded and reported", async () => {
  const calls: string[] = [];
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue(calls));
  const claim = await c.delivery(SCOPE, "plant-a", "plant-a/m1", { revision: "r7" });
  assert.equal(claim?.revision, "r7");
  assert.deepEqual(calls, ["modelUrl:plant-a/m1@r7"]);
});

test("a node id this client did not mint delivers nothing, rather than a guess", async () => {
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  assert.equal(await c.delivery(SCOPE, "plant-a", "some-other-provider/thing"), null);
});
