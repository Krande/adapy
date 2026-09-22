/**
 * The asset index: collection -> subject -> revision -> files.
 *
 * The SERVER folds it (`GET /assets/index`, `ada/assets/index.py`): a scope
 * holds tens of thousands of derived blobs and the browser must never list them
 * to answer "which revisions exist". `indexFromWire` is therefore the production
 * path. `foldListing` is the same fold over raw keys, kept as the test oracle
 * the design asks for -- a test written as a key list is far easier to read
 * than a nested wire document, and the two must agree.
 *
 * Revisions are held ASCENDING (newest last) here, whatever order the wire
 * used. The compact-UTC revision form makes lexical order chronological, so a
 * plain string sort is the whole rule.
 */

import { ASSET_PREFIX, STAGING_SEGMENT, parseAssetKey } from "./keys";
import type {
  AssetIndex,
  AssetRevision,
  AssetSubject,
  ManifestSummary,
  ResolutionMode,
  WireAssetIndex,
  WireManifestSummary,
} from "./types";

export const MANIFEST_FILENAME = "asset.json";
export const HIERARCHY_FILENAME = "hierarchy.json";

export function compareRevisions(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function summaryFromWire(m: WireManifestSummary | undefined): ManifestSummary | null {
  if (!m) return null;
  return {
    provider: m.provider,
    node: m.node ?? null,
    delivery: m.delivery,
    producedAt: m.produced_at,
    hierarchyRevision: m.hierarchy_revision ?? null,
  };
}

export function indexFromWire(wire: WireAssetIndex): AssetIndex {
  const collections = new Map<string, Map<string, AssetSubject>>();
  for (const [collection, subjects] of Object.entries(wire.collections)) {
    const out = new Map<string, AssetSubject>();
    for (const s of subjects) {
      const revisions: AssetRevision[] = s.revisions
        .map((r) => ({
          revision: r.revision,
          files: new Set(r.files) as ReadonlySet<string>,
          manifest: summaryFromWire(r.manifest),
          manifestError: r.manifest_error ?? null,
        }))
        .sort((a, b) => compareRevisions(a.revision, b.revision));
      out.set(s.subject, { collection, subject: s.subject, revisions });
    }
    collections.set(collection, out);
  }
  return { collections, malformed: wire.malformed.map((m) => m.key) };
}

/** The same fold over raw keys. A TEST ORACLE: production reads the server's. */
export function foldListing(
  keys: readonly string[],
  manifests: ReadonlyMap<string, ManifestSummary> = new Map(),
): AssetIndex {
  const tree = new Map<string, Map<string, Map<string, Set<string>>>>();
  const malformed: string[] = [];
  for (const key of keys) {
    if (!key.startsWith(`${ASSET_PREFIX}/`) || key.endsWith("/")) continue;
    if (key.startsWith(`${ASSET_PREFIX}/${STAGING_SEGMENT}/`)) continue;
    let parsed;
    try {
      parsed = parseAssetKey(key);
    } catch {
      malformed.push(key);
      continue;
    }
    const subjects = tree.get(parsed.collection) ?? new Map();
    tree.set(parsed.collection, subjects);
    const revs = subjects.get(parsed.subject) ?? new Map();
    subjects.set(parsed.subject, revs);
    const files = revs.get(parsed.revision) ?? new Set();
    revs.set(parsed.revision, files);
    files.add(parsed.filename);
  }
  const collections = new Map<string, Map<string, AssetSubject>>();
  for (const [collection, subjects] of tree) {
    const out = new Map<string, AssetSubject>();
    for (const [subject, revs] of subjects) {
      const revisions = [...revs.entries()]
        .sort(([a], [b]) => compareRevisions(a, b))
        .map(([revision, files]) => ({
          revision,
          files: files as ReadonlySet<string>,
          manifest: manifests.get(`${collection}/${subject}/${revision}`) ?? null,
          manifestError: null,
        }));
      out.set(subject, { collection, subject, revisions });
    }
    collections.set(collection, out);
  }
  return { collections, malformed };
}

const NO_SUBJECTS: ReadonlyMap<string, AssetSubject> = new Map();

export function subjectsOf(index: AssetIndex, collection: string): ReadonlyMap<string, AssetSubject> {
  return index.collections.get(collection) ?? NO_SUBJECTS;
}

/** Every distinct revision in a collection, ascending: the "run" picker's list.
 *  Small by construction -- one publish fans out to N subjects under ONE stamp. */
export function revisionsOf(index: AssetIndex, collection: string): string[] {
  const seen = new Set<string>();
  for (const s of subjectsOf(index, collection).values()) for (const r of s.revisions) seen.add(r.revision);
  return [...seen].sort(compareRevisions);
}

export function collectionsOf(index: AssetIndex): string[] {
  return [...index.collections.keys()].sort();
}

/** Open on the collection holding the newest revision, not the alphabetically
 *  first: the question on opening is "what was published most recently". */
export function defaultCollection(index: AssetIndex | null): string | null {
  if (!index) return null;
  let best: string | null = null;
  let bestRev = "";
  for (const name of collectionsOf(index)) {
    const revs = revisionsOf(index, name);
    const newest = revs[revs.length - 1] ?? "";
    if (best === null || compareRevisions(newest, bestRev) > 0) {
      best = name;
      bestRev = newest;
    }
  }
  return best;
}

/** A revision a reader may resolve to: its manifest exists. Manifests are
 *  written LAST, so a manifest-less revision is a publish that died partway --
 *  the server's tree and delivery routes skip it, and so does the browser. */
export function isComplete(rev: AssetRevision): boolean {
  return rev.files.has(MANIFEST_FILENAME);
}

/**
 * Which collection-level hierarchies make up the tops of the tree, OLDEST FIRST
 * (so a later merge leaves the newest row winning for a shared node).
 *
 * A UNION, not the newest one: each publish writes its own collection index and
 * it covers only what that publish declared. Reading only the newest collapsed
 * the tree to whatever was published last. The modes differ exactly on the
 * coeval question -- `run` means "this one publish", so only its index.
 */
export function collectionIndexRevisions(
  index: AssetIndex | null,
  collection: string | null,
  mode: ResolutionMode,
): readonly AssetRevision[] {
  if (!index || !collection) return [];
  const entry = index.collections.get(collection)?.get(collection);
  if (!entry) return [];
  const withTree = entry.revisions.filter((r) => isComplete(r) && r.files.has(HIERARCHY_FILENAME));
  if (mode.kind === "run") return withTree.filter((r) => r.revision === mode.revision);
  if (mode.kind === "as-of") return withTree.filter((r) => compareRevisions(r.revision, mode.revision) <= 0);
  return withTree;
}
