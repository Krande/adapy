// The asset surface an out-of-tree plugin may depend on — plugin API 1.6.0.
//
// WHY A REGISTRY AND NOT A ROUTE. Core reads an asset tree over REST, and for a provider whose
// data a worker can see that is the whole story. It is not the whole story for a provider that
// must be read AS THE SIGNED-IN USER: a browser-side service that authenticates the person, not
// the cluster, cannot be proxied by a worker without either handing the worker the person's
// identity or flattening every user to one service account. `registerExternalModelClient`
// (`services/externalModelClients.ts`) already carries that reasoning for external models, and
// this is the same shape for assets.
//
// NO SHIPPED PROVIDER USES IT TODAY, and that is the expected ratio. A catalogue the deployment
// itself can read -- a service principal against object storage, say -- belongs on the worker:
// the credential stays out of the page, the job cache absorbs repeat reads, and the tree still
// loads in a browser with no session. Registering here trades all three away for the one thing a
// worker cannot have, so it is the exception a provider must argue for, not the default.
//
// DISPATCH PER CALL, AND THE CALLER NEVER LEARNS WHICH. A consumer asks for a hierarchy; it does
// not ask whether a client is registered. That is deliberate: the moment a reading path branches
// on "is this provider live?", every path has to, and the two halves drift. `assetTreeSource`
// answers with something that satisfies the same interface either way.
//
// Registered by ID, where the id is the PROVIDER id the index reports -- so a plugin claims the
// provider it serves and nothing else. A provider with no registration is served by the REST
// route, which is what every core-shipped provider does.

import type {
  BuildDelivery,
  HierarchySlice,
  MeshDelivery,
  WireNodeAttributes,
} from "@/assets/types";
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
  /** OPTIONAL. What one node IS, fetched per selection.
   *
   *  Omit it unless the answer cannot be precomputed. A provider whose attributes are PUBLISHED
   *  needs nothing here -- core serves them from the store, which is one blob read and no source
   *  file, and is what every in-tree provider does. This exists for a live system of record whose
   *  properties move without a republish, where a published document would be stale the moment it
   *  was written.
   *
   *  `null` means "nothing recorded", which is an answer and not an error. */
  attributes?(
    scope: ScopeUrl,
    collection: string,
    node: string,
    opts?: { subject?: string; revision?: string },
  ): Promise<WireNodeAttributes | null>;
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

/** What one node IS, from a registered client that answers live or from the REST route.
 *
 *  `null` is "nothing recorded": a live client saying so, or the route's 404, which is the one
 *  answer for a provider that publishes no attributes, a document that does not mention the node
 *  and a node that is not published at all. A caller asking what something is cannot act
 *  differently on those, so they are not told apart here.
 *
 *  Any OTHER failure -- a 502 over an unreadable document, a network error -- is thrown, because
 *  those are not absence and a panel that showed them as "no properties" would be lying. */
export async function fetchAssetAttributes(
  scope: ScopeUrl,
  provider: string,
  collection: string,
  node: string,
  opts?: { subject?: string; revision?: string },
): Promise<WireNodeAttributes | null> {
  const client = assetTreeClient(provider);
  if (client?.attributes) return client.attributes(scope, collection, node, opts);

  const { assetsApi } = await import("@/services/api/assets");
  try {
    return await assetsApi.getAssetAttributes(scope, provider, collection, node, opts);
  } catch (e) {
    if (isNotFound(e)) return null;
    throw e;
  }
}

/** A 404 from `jsonOrThrow`, told apart from every other failure.
 *
 *  Matched on a status carried by the error where there is one, and on the message otherwise --
 *  `jsonOrThrow` formats the status into the text, and this must not depend on which of the two
 *  a given error shape happens to have. */
function isNotFound(e: unknown): boolean {
  const status = (e as { status?: unknown })?.status;
  if (typeof status === "number") return status === 404;
  return /\b404\b/.test(String((e as { message?: unknown })?.message ?? e));
}
