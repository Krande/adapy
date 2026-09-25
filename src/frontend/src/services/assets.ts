// The asset surface an out-of-tree plugin may depend on — plugin API 1.6.0.
//
// WHY A REGISTRY AND NOT A ROUTE. Core reads an asset tree over REST, and for a provider whose
// data a worker can see that is the whole story. It is not the whole story for a provider that
// must be read AS THE SIGNED-IN USER: a browser-side service that authenticates the person, not
// the cluster, cannot be proxied by a worker without either handing the worker the person's
// identity or flattening every user to one service account. That case already exists for external
// models (`registerExternalModelClient`, `services/externalModelClients.ts`), and this is the same
// shape for assets.
//
// DISPATCH PER CALL, AND THE CALLER NEVER LEARNS WHICH. A consumer asks for a hierarchy; it does
// not ask whether a client is registered. That is deliberate: the moment a reading path branches
// on "is this provider live?", every path has to, and the two halves drift. `assetTreeSource`
// answers with something that satisfies the same interface either way.
//
// Registered by ID, where the id is the PROVIDER id the index reports -- so a plugin claims the
// provider it serves and nothing else. A provider with no registration is served by the REST
// route, which is what every core-shipped provider does.

import type { BuildDelivery, HierarchySlice, MeshDelivery } from "@/assets/types";
import type { ScopeUrl } from "@/services/api/client";

/** One collection a live client can serve, in the terms the index reports. */
export interface CollectionInfo {
  readonly collection: string;
  readonly label?: string;
}

/** A browser-side live provider. Implemented by a plugin; never by core. */
export interface AssetTreeClient {
  collections(scope: ScopeUrl): Promise<readonly CollectionInfo[]>;
  hierarchy(
    scope: ScopeUrl,
    collection: string,
    opts: { root?: string; depth: number },
  ): Promise<HierarchySlice>;
  delivery(
    scope: ScopeUrl,
    collection: string,
    node: string,
    opts?: { revision?: string },
  ): Promise<MeshDelivery | BuildDelivery | null>;
}

export class AssetTreeClientError extends Error {}

const clients = new Map<string, { label: string; impl: AssetTreeClient }>();

/** Register a browser-side client for `id` (a PROVIDER id). Re-registering replaces. */
export function registerAssetTreeClient(
  id: string,
  impl: AssetTreeClient,
  opts?: { label?: string },
): void {
  const key = (id || "").trim();
  if (!key) throw new AssetTreeClientError("provider id must be a non-empty string");
  if (!impl) throw new AssetTreeClientError(`no implementation given for provider ${key}`);
  clients.set(key, { label: opts?.label || key, impl });
}

export function unregisterAssetTreeClient(id: string): void {
  clients.delete((id || "").trim());
}

/** The client registered for `id`, or null when this provider is served by a worker.
 *
 *  Exported so a caller that MUST branch -- a diagnostics panel, a test -- can, while the
 *  ordinary read paths never make the caller ask. */
export function assetTreeClient(id: string): AssetTreeClient | null {
  return clients.get((id || "").trim())?.impl ?? null;
}

/** Every registered client, for a panel that lists what this deployment can reach. */
export function registeredAssetTreeClients(): readonly { id: string; label: string }[] {
  return [...clients.entries()].map(([id, v]) => ({ id, label: v.label })).sort((a, b) => a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------------------------------------
// Dispatch. The only thing a reading path should call.
// ---------------------------------------------------------------------------------------------

/** One hierarchy slice for `provider`, from its registered client where there is one and from the
 *  REST route otherwise.
 *
 *  The two halves answer the SAME type, so the caller has nothing to branch on -- which is the
 *  point. A live client returns the model directly (it built it); the route returns wire, which
 *  `parseHierarchySlice` validates exactly as it does for any other provider, because a plugin's
 *  server is no more trusted than core's own. */
export async function fetchAssetHierarchy(
  scope: ScopeUrl,
  provider: string,
  collection: string,
  opts: { root?: string | null; revision: string; depth?: number },
): Promise<HierarchySlice> {
  const client = assetTreeClient(provider);
  if (client) {
    return client.hierarchy(scope, collection, {
      root: opts.root ?? undefined,
      // A live client is asked for a depth, never a revision: it reads a system of record that
      // has no revisions to ask about -- that is what makes it live. `revision` is the published
      // half's vocabulary and is dropped here rather than invented.
      depth: opts.depth ?? 1,
    });
  }
  const { assetsApi } = await import("@/services/api/assets");
  const { parseHierarchySlice } = await import("@/assets/projection");
  return parseHierarchySlice(await assetsApi.getAssetTree(scope, provider, collection, opts));
}

/** The delivery claim for one node, from a registered client or the REST route.
 *
 *  `null` is a real answer from a live client -- "this node has nothing to deliver" -- and is
 *  passed through rather than turned into an error. The route has no such case: it 404s, which
 *  `jsonOrThrow` raises. */
export async function fetchAssetDelivery(
  scope: ScopeUrl,
  provider: string,
  collection: string,
  node: string,
  opts?: { revision?: string },
): Promise<MeshDelivery | BuildDelivery | null> {
  const client = assetTreeClient(provider);
  if (client) return client.delivery(scope, collection, node, opts);

  const { assetsApi } = await import("@/services/api/assets");
  const { parseDeliveryClaim } = await import("@/assets/delivery");
  return parseDeliveryClaim(await assetsApi.getAssetDelivery(scope, provider, collection, node, opts));
}
