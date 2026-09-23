// The Assets tab's fetch side: index -> collection indexes -> spines on expand.
//
// React-free and injected with its API so it can be driven under `node --test`
// against canned documents. Every writer below goes through the store's
// actions; nothing here holds state of its own except a cache of collection
// index SLICES, kept so a re-merge in the right order never refetches.
//
// ORDER MATTERS FOR THE COLLECTION INDEX. The tops of the tree are a union of
// every index the mode admits, merged OLDEST FIRST so the newest word about a
// shared node wins. When a mode change admits an index older than one already
// merged, the newer ones are re-merged after it from the cache.

import { collectionIndexRevisions, compareRevisions, defaultCollection, indexFromWire, subjectsOf } from "@/assets/assetIndex";
import type { SourceNodesAnswer } from "@/assets/changes";
import { parseHierarchySlice } from "@/assets/projection";
import type { SpineSource } from "@/assets/spines";
import type { AssetNode, WireAssetIndex, WireHierarchySlice } from "@/assets/types";

import type { AssetBrowserState } from "./assetBrowserStore";

export interface AssetsApiLike {
  getAssetIndex(scope: string, collection?: string): Promise<WireAssetIndex>;
  getAssetTree(
    scope: string,
    provider: string,
    collection: string,
    opts: { root?: string | null; revision: string },
  ): Promise<WireHierarchySlice>;
}

/** The change feed's fetch side (`services/api/sourceNodes`), injected the
 *  same way `AssetsApiLike` is -- so this loader stays drivable under
 *  `node --test` against canned answers, and so a caller that does not care
 *  about the change feed (most of today's tests) need not supply one at all:
 *  evidence fetching is then simply a no-op, never a crash. */
export interface SourceNodesApiLike {
  getSourceNodes(scope: string, source: string, refs: readonly string[]): Promise<SourceNodesAnswer | null>;
}

export interface StoreLike {
  getState(): AssetBrowserState;
}

/** Phase 2 browses published collections only; live providers arrive with
 *  delivery (Phase 3) through the same route with their own id. */
const PROVIDER = "published";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function createAssetBrowserLoader(store: StoreLike, api: AssetsApiLike, sourceNodesApi?: SourceNodesApiLike) {
  const indexSlices = new Map<string, readonly AssetNode[]>(); // `${collection}@${revision}`
  let generation = 0; // bumped on scope/collection change; stale responses are dropped

  const alive = (gen: number, scope: string, collection: string | null) => {
    const s = store.getState();
    return gen === generation && s.scope === scope && (collection === null || s.collection === collection);
  };

  /** Ask the change feed about `refs` under `source` (a provider id), and
   *  fold the answer into the store. Best-effort: a hiccup here must not read
   *  as a hierarchy-fetch failure -- the spine or index fetch it rides along
   *  with has ALREADY SUCCEEDED by the time this runs, and the change feed is
   *  supplementary. Refs already in `evidenceAsked` are dropped before the
   *  request, the dedup `loadSpine`'s own re-fetch guard already relies on
   *  for hierarchy slices, applied here to the refs granularity instead of
   *  the spine-root granularity. On failure nothing is marked asked, so the
   *  NEXT spine load or root sync retries rather than black-holing a
   *  transient error into a permanent "no-feed". */
  async function loadEvidence(scope: string, source: string, refs: readonly string[]): Promise<void> {
    if (!sourceNodesApi || !refs.length) return;
    const gen = generation;
    const s = store.getState();
    const collection = s.collection;
    const missing = refs.filter((r) => !s.evidenceAsked.has(r));
    if (!missing.length) return;
    try {
      const answer = await sourceNodesApi.getSourceNodes(scope, source, missing);
      if (!alive(gen, scope, collection)) return;
      store.getState().mergeSourceAnswer(source, answer, missing);
    } catch {
      // Best-effort; see the function comment. Nothing is marked asked.
    }
  }

  /** Every published root (a resolved subject other than the collection
   *  itself) gets asked about EAGERLY, independent of whether its row has
   *  ever been expanded -- the root-state chip must not wait on the user
   *  opening a branch. Bounded by the number of published subjects (typically
   *  tens), never by the size of any subject's own subtree, which is what
   *  keeps this "cheap and eager" rather than "ask for 41k refs". Grouped by
   *  each subject's OWN manifest provider, because `source` names an external
   *  system and core has no truer identifier for "whose feed is this" than
   *  the provider that published the subject. */
  async function loadRootEvidence(scope: string, collection: string): Promise<void> {
    if (!sourceNodesApi) return;
    const s = store.getState();
    if (!s.index) return;
    const byProvider = new Map<string, string[]>();
    for (const [subject, entry] of subjectsOf(s.index, collection)) {
      if (subject === collection) continue; // the collection's own index entry, not an export root
      const newest = [...entry.revisions].reverse().find((r) => r.manifest);
      if (!newest?.manifest) continue; // nothing complete published here yet -- nothing to ask about
      const refs = byProvider.get(newest.manifest.provider) ?? [];
      refs.push(subject);
      byProvider.set(newest.manifest.provider, refs);
    }
    for (const [provider, refs] of byProvider) await loadEvidence(scope, provider, refs);
  }

  async function loadCollections(scope: string): Promise<void> {
    const gen = ++generation;
    const s = store.getState();
    s.setIndexLoading(true);
    try {
      const all = indexFromWire(await api.getAssetIndex(scope));
      if (!alive(gen, scope, null)) return;
      const names = [...all.collections.keys()].sort();
      const cur = store.getState();
      cur.setCollections(names);
      const keep = cur.collection && names.includes(cur.collection) ? cur.collection : defaultCollection(all);
      cur.setCollection(keep);
      if (keep) await openCollection(scope, keep);
    } catch (e) {
      if (alive(gen, scope, null)) store.getState().setIndexError(message(e));
    } finally {
      if (gen === generation) store.getState().setIndexLoading(false);
    }
  }

  async function openCollection(scope: string, collection: string): Promise<void> {
    const gen = generation;
    try {
      const index = indexFromWire(await api.getAssetIndex(scope, collection));
      if (!alive(gen, scope, collection)) return;
      store.getState().setIndex(index);
      await syncCollectionIndexes(scope);
    } catch (e) {
      if (alive(gen, scope, collection)) store.getState().setIndexError(message(e));
    }
  }

  /** Bring the merged collection indexes in line with what the mode admits. */
  async function syncCollectionIndexes(scope: string): Promise<void> {
    const gen = generation;
    const s = store.getState();
    const collection = s.collection;
    if (!collection || !s.index) return;
    const wanted = collectionIndexRevisions(s.index, collection, s.mode).map((r) => r.revision);
    const merged = s.mergedIndexRevisions;
    const missing = wanted.filter((r) => !merged.includes(r));
    if (!missing.length) {
      if (wanted.length !== merged.length || wanted.some((r, i) => r !== merged[i])) {
        // Nothing to fetch; only the admitted set changed (e.g. `run` narrowing).
        s.setMergedIndexRevisions(wanted);
      }
      // Root evidence is asked unconditionally on every sync, including this
      // no-new-index-to-fetch path (a `run` narrowing, or simply the first
      // sync after `openCollection` set the index): `loadRootEvidence` is
      // idempotent per ref (`evidenceAsked`), so the extra call costs nothing
      // once the refs are already known, and is the only way a fresh
      // collection's roots get asked at all.
      await loadRootEvidence(scope, collection);
      return;
    }
    const fetched = await Promise.all(
      missing.map(async (revision) => {
        const key = `${collection}@${revision}`;
        if (!indexSlices.has(key)) {
          const slice = parseHierarchySlice(await api.getAssetTree(scope, PROVIDER, collection, { revision }));
          indexSlices.set(key, slice.nodes);
        }
        return revision;
      }),
    );
    if (!alive(gen, scope, collection)) return;
    // Re-merge from the oldest newly-fetched revision upward, so a late older
    // index cannot overwrite a newer one's rows.
    const oldestNew = [...fetched].sort(compareRevisions)[0];
    const cur = store.getState();
    for (const revision of wanted) {
      if (compareRevisions(revision, oldestNew) < 0) continue;
      cur.mergeSlice(indexSlices.get(`${collection}@${revision}`) ?? [], {
        subject: collection,
        revision,
        root: null,
      });
    }
    cur.setMergedIndexRevisions(wanted);
    await loadRootEvidence(scope, collection);
  }

  /** Fetch and merge one subtree spine. Idempotent per (root, revision). */
  async function loadSpine(scope: string, source: SpineSource): Promise<void> {
    const gen = generation;
    const s = store.getState();
    const collection = s.collection;
    if (!collection) return;
    if (s.spineLoading.has(source.root) || s.spineLoaded.get(source.root) === source.revision) return;
    s.beginSpine(source.root);
    try {
      const wire = await api.getAssetTree(scope, PROVIDER, collection, {
        root: source.root,
        revision: source.revision,
      });
      if (!alive(gen, scope, collection)) return;
      const slice = parseHierarchySlice(wire);
      const cur = store.getState();
      cur.mergeSlice(slice.nodes, { subject: source.subject, revision: source.revision, root: source.root });
      cur.endSpine(source.root, source.revision);
      // Per-node evidence, lazily: exactly this spine's own refs (its root
      // plus whatever it just brought in), never the whole tree. `wire.provider`
      // is who produced this slice -- and so who would know whether it moved.
      await loadEvidence(scope, wire.provider, [source.root, ...slice.nodes.map((n) => n.id)]);
    } catch (e) {
      if (alive(gen, scope, collection)) store.getState().failSpine(source.root, message(e));
    }
  }

  /** Fetch every listed spine -- what "place the pending subjects" does. A few
   *  at a time: each can be megabytes, and the point is to finish, not to race. */
  async function loadSpines(scope: string, sources: readonly SpineSource[], concurrency = 3): Promise<void> {
    const queue = [...sources];
    const worker = async () => {
      for (let next = queue.shift(); next; next = queue.shift()) await loadSpine(scope, next);
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, worker));
  }

  /** Switch collection: invalidates in-flight responses for the old one. */
  async function chooseCollection(scope: string, collection: string): Promise<void> {
    generation++;
    store.getState().setCollection(collection);
    await openCollection(scope, collection);
  }

  /** Refresh: rebuild the collection's forest from nothing. */
  async function refresh(scope: string): Promise<void> {
    const s = store.getState();
    if (!s.collection) return loadCollections(scope);
    generation++;
    for (const k of [...indexSlices.keys()]) if (k.startsWith(`${s.collection}@`)) indexSlices.delete(k);
    s.resetForest();
    await openCollection(scope, s.collection);
  }

  return {
    loadCollections,
    openCollection,
    syncCollectionIndexes,
    loadSpine,
    loadSpines,
    chooseCollection,
    refresh,
    loadEvidence,
    loadRootEvidence,
  };
}

export type AssetBrowserLoader = ReturnType<typeof createAssetBrowserLoader>;

const LOADERS = new WeakMap<object, AssetBrowserLoader>();

/** One loader per store instance, so its slice cache and generation counter
 *  survive the tab being unmounted and remounted. `sourceNodesApi` is read
 *  only on the FIRST call for a given store -- the same one-loader-per-store
 *  rule the cache and generation counter already follow. */
export function loaderFor(store: StoreLike, api: AssetsApiLike, sourceNodesApi?: SourceNodesApiLike): AssetBrowserLoader {
  let loader = LOADERS.get(store);
  if (!loader) {
    loader = createAssetBrowserLoader(store, api, sourceNodesApi);
    LOADERS.set(store, loader);
  }
  return loader;
}
