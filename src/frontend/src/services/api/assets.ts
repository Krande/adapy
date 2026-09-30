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
  WireNodeAttributes,
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
   *  -- it resolves revisions itself, from the index, so the server never picks.
   *
   *  `parent` narrows the answer to ONE LEVEL of that spine: the rows whose
   *  parent is `parent`, each with a trailing `children` column (its own child
   *  count in the spine). `parent === root` is the spine's first level. */
  async getAssetTree(
    scope: ScopeUrl,
    provider: string,
    collection: string,
    opts: { root?: string | null; revision: string; parent?: string | null },
  ): Promise<WireHierarchySlice> {
    const q = new URLSearchParams({ revision: opts.revision });
    if (opts.root) q.set("root", opts.root);
    if (opts.parent) q.set("parent", opts.parent);
    const url = `${base(scope)}/tree/${encodeURIComponent(provider)}/${encodeURIComponent(collection)}?${q}`;
    const r = await authedFetch(url);
    const what = `${opts.root ?? "index"}@${opts.revision}${opts.parent ? ` under ${opts.parent}` : ""}`;
    return jsonOrThrow<WireHierarchySlice>(r, `getAssetTree(${collection}, ${what})`);
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

  /** What one node IS. `subject` names the manifest-owning ancestor when the
   *  node was published under a root rather than in its own right -- the same
   *  parameter the build request takes, resolved from the row's badge.
   *
   *  Throws on 404, which is this route's "nothing recorded"; the caller that
   *  renders a selection treats that as absence rather than failure. */
  async getAssetAttributes(
    scope: ScopeUrl,
    provider: string,
    collection: string,
    node: string,
    opts?: { subject?: string; revision?: string },
  ): Promise<WireNodeAttributes> {
    const q = new URLSearchParams();
    if (opts?.subject) q.set("subject", opts.subject);
    if (opts?.revision) q.set("revision", opts.revision);
    const qs = q.toString() ? `?${q.toString()}` : "";
    const url = `${base(scope)}/attributes/${encodeURIComponent(provider)}/${encodeURIComponent(collection)}/${encodeURIComponent(node)}${qs}`;
    const r = await authedFetch(url);
    return jsonOrThrow<WireNodeAttributes>(r, `getAssetAttributes(${collection}/${node})`);
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

  /** Publish what is staged under `assets/_staging/<staging_id>/` as `provider`'s
   *  format: the provider derives, core writes (`POST /assets/publish`). A job:
   *  poll it, then read `derived_key` for the outcome. */
  async publishStaged(
    scope: ScopeUrl,
    body: { provider: string; staging_id: string; dry_run?: boolean; replace?: boolean },
  ): Promise<{ job_id: string; derived_key: string; dry_run: boolean }> {
    const r = await authedFetch(`${base(scope)}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow(r, `publishStaged(${body.staging_id})`);
  },

  /** What is staged and not yet published, grouped by staging id -- the store's
   *  memory of a request or upload nobody finished publishing. */
  async listStaging(scope: ScopeUrl): Promise<{
    staged: { staging_id: string; files: { file: string; key: string; size: number | null }[]; size: number }[];
  }> {
    const r = await authedFetch(`${base(scope)}/staging`);
    return jsonOrThrow(r, "listStaging");
  },

  /** Every file in this scope's asset areas -- published, staged and cached builds -- classified. */
  async listFiles(scope: ScopeUrl): Promise<WireAssetFiles> {
    const r = await authedFetch(`${base(scope)}/files`);
    return jsonOrThrow(r, "listFiles");
  },

  /** Delete ONE staged or cached-build file. A published file answers 409 naming its revision:
   *  it goes only with that revision (`unpublishRevision`). */
  async deleteFile(scope: ScopeUrl, key: string): Promise<{ ok: boolean; deleted: string[] }> {
    const r = await authedFetch(`${base(scope)}/files?key=${encodeURIComponent(key)}`, { method: "DELETE" });
    return jsonOrThrow(r, `deleteFile(${key})`);
  },

  /** Unpublish one subject-revision -- every file in it. Refused (409, with `reason`) while another
   *  manifest still names one of them. */
  async unpublishRevision(
    scope: ScopeUrl,
    collection: string,
    subject: string,
    revision: string,
  ): Promise<{ ok: boolean; deleted: string[]; reason: string | null; held_by: string[] }> {
    const r = await authedFetch(
      `${base(scope)}/${encodeURIComponent(collection)}/${encodeURIComponent(subject)}/${encodeURIComponent(revision)}`,
      { method: "DELETE" },
    );
    if (r.status === 409) return r.json();
    return jsonOrThrow(r, `unpublishRevision(${collection}/${subject}@${revision})`);
  },
};

/** One source: one provider's publish into one collection at one revision, with what was derived. */
export interface WireAssetSource {
  collection: string;
  revision: string;
  provider: string;
  /** The source file(s) it stored, else what it is (`tree-<provider>`). */
  label: string;
  subjects: number;
  size: number;
  derived_files: number;
  derived_size: number;
  last_modified: string | null;
}

export const assetSourcesApi = {
  async list(scope: ScopeUrl): Promise<{ sources: WireAssetSource[]; orphans: { key: string; size: number }[] }> {
    const r = await authedFetch(`${base(scope)}/sources`);
    return jsonOrThrow(r, "listSources");
  },
  async files(
    scope: ScopeUrl,
    s: Pick<WireAssetSource, "collection" | "revision" | "provider">,
  ): Promise<WireAssetSource & { published: { key: string; size: number }[]; derived: { key: string; size: number }[] }> {
    const r = await authedFetch(
      `${base(scope)}/sources/${encodeURIComponent(s.collection)}/${encodeURIComponent(s.revision)}?provider=${encodeURIComponent(s.provider)}`,
    );
    return jsonOrThrow(r, "sourceFiles");
  },
  /** Delete the source and everything derived from it. 409 (with `reason`, `held_by`) while another
   *  source still references one of its files. */
  async remove(
    scope: ScopeUrl,
    s: Pick<WireAssetSource, "collection" | "revision" | "provider">,
  ): Promise<{ ok: boolean; deleted?: string[]; reason?: string; held_by?: string[] }> {
    const r = await authedFetch(
      `${base(scope)}/sources/${encodeURIComponent(s.collection)}/${encodeURIComponent(s.revision)}?provider=${encodeURIComponent(s.provider)}`,
      { method: "DELETE" },
    );
    if (r.status === 409) return r.json();
    return jsonOrThrow(r, "deleteSource");
  },
};

export interface WireAssetFile {
  key: string;
  size: number;
  last_modified: string | null;
  area: "published" | "staged" | "derived";
  /** Derived only: a cached `build`, or a publish job's `publish-summary`. */
  kind?: "build" | "publish-summary";
  job?: string;
  file: string;
  collection?: string;
  subject?: string;
  revision?: string;
  staging_id?: string;
  provider?: string;
  node?: string | null;
  fingerprint?: string;
}

export interface WireAssetFiles {
  files: WireAssetFile[];
  unrecognised: { key: string; size: number; last_modified: string | null }[];
  totals: Record<string, { files: number; size: number }>;
}
