// A flat model catalogue, seen as an asset tree.
//
// THE TWO SHAPES. `ExternalModelClient` is flat -- collections hold models, and `modelUrl` hands
// back a URL. `AssetTreeClient` is a tree -- a hierarchy of nodes, and a delivery CLAIM per node.
// The mesh catalogue already implements the first (it must authenticate as the signed-in user,
// which is why it is a browser-side client at all), and making it implement the second as well
// would mean one provider maintaining two descriptions of the same catalogue.
//
// So core ships the adapter instead. One direction only: flat -> tree. A catalogue is a tree of
// depth one, which is not a limitation being worked around -- it is what a catalogue IS, and the
// Assets tab renders it beside a deep CSG hierarchy without either knowing about the other.
//
// GEOMETRY KIND IS NOT THE AXIS HERE. A catalogue yields `mesh` claims because it stores built
// models; a CSG provider yields `build` claims because it stores the inputs to a build. Both hang
// off the same tree and both are read through the same two calls -- the kind is what the claim
// SAYS, not a second system.

import type { AssetNode, BuildDelivery, HierarchySlice, MeshDelivery } from "@/assets/types";
import type { ScopeUrl } from "@/services/api/client";
import type { AssetTreeClient, CollectionInfo } from "@/services/assets";
import type { ExternalModelClient } from "@/services/externalModelClients";

/** The synthetic root every catalogue collection gets.
 *
 *  A catalogue has no root node of its own -- it is a bag of models -- but a hierarchy needs one
 *  to hang them from, and `null` root means "index" rather than "subtree". Naming it after the
 *  collection keeps ids unique across a mixed provider list. */
export const catalogueRootId = (collection: string): string => `${collection}`;

function modelNode(collection: string, id: string, label: string, provider: string): AssetNode {
  return {
    id: `${collection}/${id}`,
    parent: catalogueRootId(collection),
    label,
    // The provider's own node type, shown and filtered on, never branched on. A catalogue has
    // exactly one.
    kind: "model",
    leaf: true,
    delivery: "mesh",
    provider,
  };
}

/** Wrap an `ExternalModelClient` so it satisfies `AssetTreeClient`.
 *
 *  `provider` is the id the asset index reports for this catalogue -- the same id the client is
 *  registered under, so a node's `provider` field and the registry agree. */
export function assetTreeClientFromCatalogue(provider: string, client: ExternalModelClient): AssetTreeClient {
  return {
    async collections(_scope: ScopeUrl): Promise<readonly CollectionInfo[]> {
      const cols = await client.listCollections();
      return cols.map((c) => ({ collection: c.id, label: c.name }));
    },

    async hierarchy(
      _scope: ScopeUrl,
      collection: string,
      opts: { root?: string; depth: number },
    ): Promise<HierarchySlice> {
      const models = await client.listModels(collection);
      const root = catalogueRootId(collection);

      // A catalogue is one level deep, so a request for a SUBTREE of the root is the same set as
      // the index, and a request rooted anywhere else is empty rather than an error: asking for
      // the children of a leaf is a fair question with the answer "none".
      const rooted = opts.root != null && opts.root !== root;
      const nodes: AssetNode[] = rooted
        ? []
        : models.map((m) => modelNode(collection, m.id, m.name || m.id, provider));

      return {
        schema: "ada.assets/hierarchy@1",
        provider,
        collection,
        // `null` claims no completeness, which is the honest answer for a listing that may be
        // paged or filtered upstream; asked for the root explicitly, this IS the whole subtree.
        root: opts.root ?? null,
        producedAt: new Date().toISOString(),
        depth: 1,
        nodes,
      };
    },

    async delivery(
      _scope: ScopeUrl,
      collection: string,
      node: string,
      opts?: { revision?: string },
    ): Promise<MeshDelivery | BuildDelivery | null> {
      // Node ids are `<collection>/<model>`; the root itself delivers nothing.
      const modelId = node.startsWith(`${collection}/`) ? node.slice(collection.length + 1) : null;
      if (!modelId) return null;

      const { url, headers } = await client.modelUrl(collection, modelId, { revision: opts?.revision });
      return {
        kind: "mesh",
        url,
        headers,
        // A catalogue stores what an exporter wrote; core's GLB convention is z-up and the
        // external-model path already assumes it, so this stays consistent with how the same
        // file loads through the Files tab.
        sourceUpAxis: "z",
        // A provider without revisions reports the one it served: "current". Inventing a
        // revision id here would make an unversioned catalogue look versioned.
        revision: opts?.revision ?? "current",
        provider,
      };
    },
  };
}
