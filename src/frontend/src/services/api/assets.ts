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
import type { WireAssetIndex, WireHierarchySlice, WireProvider } from "@/assets/types";

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
};
