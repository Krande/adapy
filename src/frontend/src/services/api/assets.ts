// The asset browser routes: /api/scopes/{scope}/assets/...
//
// Deliberately NOT spread into the `viewerApi` facade: that object is
// re-exported to out-of-tree plugins, and the asset surface a plugin may depend
// on arrives with plugin API 1.6.0 (`registerAssetTreeClient`), not as a side
// effect of the core tab shipping. Core imports `assetsApi` directly.
//
// Returns WIRE shapes; `@/assets/projection` and `@/assets/assetIndex` turn them
// into the model every other module reads.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";
import { filesApi } from "./files";
import type {
  WireAssetIndex,
  WireBuildAssetResponse,
  WireDeliveryClaim,
  WireHierarchySlice,
  WireProvider,
} from "@/assets/types";

function base(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets`;
}

/** The built-in path that serves every provider publishing under the key
 *  grammar. The tab browses published collections through it in Phase 2. */
export const PUBLISHED_PROVIDER = "published";

export const assetsApi = {
  async listAssetProviders(scope: ScopeUrl): Promise<WireProvider[]> {
    const r = await authedFetch(`${base(scope)}/providers`);
    return (await jsonOrThrow<{ providers: WireProvider[] }>(r, `listAssetProviders(${scope})`)).providers;
  },

  /** The server-folded index. With `collection`, one bounded listing of that
   *  collection plus the per-revision manifest summaries the badges read;
   *  without, the whole `assets/` prefix (collections only -- used to pick one). */
  async getAssetIndex(scope: ScopeUrl, collection?: string): Promise<WireAssetIndex> {
    const q = new URLSearchParams();
    if (collection) {
      q.set("collection", collection);
      q.set("manifests", "true");
    }
    const qs = q.toString();
    const r = await authedFetch(`${base(scope)}/index${qs ? `?${qs}` : ""}`);
    return jsonOrThrow<WireAssetIndex>(r, `getAssetIndex(${scope}, ${collection ?? "*"})`);
  },

  /** One hierarchy slice. `root` absent = the collection index at `revision`;
   *  present = that root's subtree spine. `revision` is always passed by the tab
   *  -- it resolves revisions itself, from the index, so the server never picks. */
  async getAssetTree(
    scope: ScopeUrl,
    provider: string,
    collection: string,
    opts: { root?: string | null; revision: string },
  ): Promise<WireHierarchySlice> {
    const q = new URLSearchParams({ revision: opts.revision });
    if (opts.root) q.set("root", opts.root);
    const url = `${base(scope)}/tree/${encodeURIComponent(provider)}/${encodeURIComponent(collection)}?${q}`;
    const r = await authedFetch(url);
    return jsonOrThrow<WireHierarchySlice>(r, `getAssetTree(${collection}, ${opts.root ?? "index"}@${opts.revision})`);
  },

  /** The delivery claim for one node -- `mesh` or `build`. `node` is the
   *  MANIFEST-OWNING subject, not necessarily the row a caller clicked: a row
   *  covered by an ancestor's publish (a `ghost`/`below` badge in `rowFacts`)
   *  asks here with the ancestor's subject, because this route reads one
   *  manifest by its own key and never walks ancestors itself. */
  async getAssetDelivery(
    scope: ScopeUrl,
    provider: string,
    collection: string,
    node: string,
    opts?: { revision?: string },
  ): Promise<WireDeliveryClaim> {
    const q = opts?.revision ? `?revision=${encodeURIComponent(opts.revision)}` : "";
    const url = `${base(scope)}/delivery/${encodeURIComponent(provider)}/${encodeURIComponent(collection)}/${encodeURIComponent(node)}${q}`;
    const r = await authedFetch(url);
    return jsonOrThrow<WireDeliveryClaim>(r, `getAssetDelivery(${collection}/${node})`);
  },

  /** Build one node's geometry on demand (or find it already built). Body
   *  mirrors the REST route: `node` is the node actually built -- it rides
   *  into the derived key and fingerprint on every call (Decision 3), so two
   *  different nodes covered by the same ancestor are two builds. `subject`
   *  names the manifest-owning ancestor when it differs from `node` (a
   *  covered load); omitted, the route defaults it to `node`. `cached: true`
   *  + `job_id: null` means the summary named by the returned `derived_key`
   *  already exists -- nothing was enqueued, and `./assets/delivery` reads
   *  it straight from there. */
  async buildAssetNode(
    scope: ScopeUrl,
    body: { provider: string; collection: string; node: string; subject?: string; revision?: string; force?: boolean },
  ): Promise<WireBuildAssetResponse> {
    const r = await authedFetch(`${base(scope)}/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow<WireBuildAssetResponse>(r, `buildAssetNode(${body.collection}/${body.node})`);
  },

  /** A build summary blob (`ada.assets/build@1`). Gzip-at-rest like every
   *  other derived JSON document in this scope -- the blob route forwards
   *  `Content-Encoding: gzip` and `fetch` decompresses it before `.json()`
   *  ever runs (`fetchProceduralRelocations` in `./procedural` is the same
   *  pattern), so there is no gunzip to hand-roll here. */
  async getBuildSummary(scope: ScopeUrl, key: string): Promise<unknown> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    return jsonOrThrow<unknown>(r, `getBuildSummary(${key})`);
  },
};
