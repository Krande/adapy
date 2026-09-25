// An `ExternalModelClient`, seen through the asset tree's fetching abstraction.
//
// WHAT CORE MUST NOT KNOW. How a provider stores its tree -- a CSG database, a catalogue of
// exported models, a live system of record -- is the provider's business. Core asks two
// questions: what are the roots, and what is under this one. `AssetTreeClient` IS that
// abstraction, and a provider whose format has structure implements it directly; core never
// learns what produced the geometry.
//
// So this adapter is narrow on purpose. It exists only because the mesh catalogue ALREADY
// implements `ExternalModelClient` -- it has to, to authenticate as the signed-in user -- and
// making it describe the same tree twice is the duplication that stays consistent until it does
// not. It translates one interface into the other and asserts nothing about shape beyond what
// the wrapped client can actually answer.
//
// `listModels` is a list of ROOTS. Not leaves, and not children of some invented parent: each is
// a top-level node of that collection's tree. Whether a root has anything under it is a question
// for the provider, and this particular client has no call that answers it -- so its roots report
// `leaf: true` because THIS CLIENT offers no deeper fetch, not because catalogues are shallow.
// A catalogue that grows a subtree call stops needing this adapter and implements the interface.
//
// GEOMETRY KIND IS NOT THE AXIS. A stored model yields a `mesh` claim; a provider holding the
// inputs to a build yields `build`. Same tree, same two calls -- the kind is what the claim SAYS.

import type { AssetNode, BuildDelivery, HierarchySlice, MeshDelivery } from "@/assets/types";
import type { ScopeUrl } from "@/services/api/client";
import type { AssetTreeClient, CollectionInfo } from "@/services/assets";
import type { ExternalModelClient } from "@/services/externalModelClients";

/** Node ids are `<collection>/<model>`, so ids stay unique across a mixed provider list. */
const nodeId = (collection: string, modelId: string): string => `${collection}/${modelId}`;

function rootNode(collection: string, id: string, label: string, provider: string): AssetNode {
  return {
    id: nodeId(collection, id),
    // A ROOT. The collection is not a node -- it is the thing being listed.
    parent: null,
    label,
    // The provider's own node type: shown, filtered on, never branched on.
    kind: "model",
    // True of THIS CLIENT, which exposes no way to ask for children -- not a claim about the
    // storage format behind it.
    leaf: true,
    delivery: "mesh",
    provider,
  };
}

/** Wrap an `ExternalModelClient` so it answers the asset tree's two questions.
 *
 *  `provider` is the id the asset index reports, and the id the client is registered under, so a
 *  node's `provider` field and the registry agree. */
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
      // A ROOTED request asks what is under one node. This client cannot answer that -- it has
      // no subtree call -- and the honest reply is an empty subtree for a node it reported as a
      // leaf, not an error. A consumer walking a mixed tree cannot know in advance which
      // providers are deep, and a throw would make this one break the walk.
      const nodes: AssetNode[] =
        opts.root != null
          ? []
          : (await client.listModels(collection)).map((m) => rootNode(collection, m.id, m.name || m.id, provider));

      return {
        schema: "ada.assets/hierarchy@1",
        provider,
        collection,
        root: opts.root ?? null,
        producedAt: new Date().toISOString(),
        // What was actually fetched. The wrapped client returns the roots in one call and has no
        // second level to descend into, so a deeper `opts.depth` cannot be honoured and is not
        // claimed to have been.
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
      const prefix = `${collection}/`;
      const modelId = node.startsWith(prefix) ? node.slice(prefix.length) : null;
      // A node id this client did not mint: nothing to deliver, rather than a guess.
      if (!modelId) return null;

      const { url, headers } = await client.modelUrl(collection, modelId, { revision: opts?.revision });
      return {
        kind: "mesh",
        url,
        headers,
        // Core's GLB convention, and what the same file already assumes when loaded through the
        // Files tab -- so one model does not arrive oriented two ways.
        sourceUpAxis: "z",
        // A provider without revisions reports the one it served. Minting an id would make an
        // unversioned catalogue look versioned and offer history that does not exist.
        revision: opts?.revision ?? "current",
        provider,
      };
    },
  };
}
