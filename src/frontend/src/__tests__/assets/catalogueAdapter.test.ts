// A flat model catalogue seen as an asset tree.
//
// The adapter exists so one provider does not maintain two descriptions of the same catalogue:
// the mesh catalogue already implements `ExternalModelClient` (it must authenticate as the
// signed-in user), and core turns that into the tree shape rather than asking the plugin to
// implement a second interface.
//
// What matters is that a catalogue and a deep CSG hierarchy are read through the SAME two calls
// and differ only in what the delivery claim says -- `mesh` for a stored model, `build` for a
// provider that stores the inputs to a build.

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

const { assetTreeClientFromCatalogue, catalogueRootId } = await import("@/services/assetCatalogueAdapter");

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

test("a catalogue is a tree of depth one, and says so", async () => {
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  const slice = await c.hierarchy(SCOPE, "plant-a", { depth: 5 });

  assert.equal(slice.schema, "ada.assets/hierarchy@1");
  assert.equal(slice.provider, "mesh-catalogue");
  assert.equal(slice.depth, 1, "a catalogue has no deeper structure to report");
  assert.equal(slice.nodes.length, 2);
  assert.deepEqual(
    slice.nodes.map((n) => [n.id, n.parent, n.label, n.leaf, n.delivery]),
    [
      ["plant-a/m1", catalogueRootId("plant-a"), "Deck", true, "mesh"],
      // An unnamed model falls back to its id rather than rendering as a blank row.
      ["plant-a/m2", catalogueRootId("plant-a"), "m2", true, "mesh"],
    ],
  );
  // Every node names the provider that produced it, so a mixed collection reads like a
  // single-source one.
  assert.ok(slice.nodes.every((n) => n.provider === "mesh-catalogue"));
});

test("asking for the children of a leaf is a fair question with the answer 'none'", async () => {
  // Not an error: a consumer walking a mixed tree cannot know in advance which providers are
  // deep, and a throw here would make the catalogue the one that breaks the walk.
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

test("the collection root itself delivers nothing", async () => {
  const c = assetTreeClientFromCatalogue("mesh-catalogue", catalogue());
  assert.equal(await c.delivery(SCOPE, "plant-a", catalogueRootId("plant-a")), null);
});
