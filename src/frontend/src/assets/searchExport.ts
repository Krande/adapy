// Pure helpers for exporting attribute-search hits and for walking what an export wrote.
//
// AN EXPORT WRITES A FOLDER, NOT A FILE. Per hit: `<folder>/<collection>/<name>.ifc`, beside it a
// `<name>.meta.json` (where it came from) and maybe a `<name>.thumb.png`, optionally a `.glb` /
// `.step`; and once per folder `<folder>/_export.json`, the files in export order. The scope's file
// listing hides the sidecars and the `_export.json` (they are not things to open), so everything
// here that needs one derives its key from the file's -- the one rule both sides keep: replace the
// file's LAST extension (`x/y/A.ifc` -> `x/y/A.meta.json`).
//
// No fetching, no stores: the dialog, the runs list and the gallery call these with what they read.

import type {
  ExportDoc,
  ExportJoint,
  ExportPreflight,
  ExportRequestBatch,
  ExportRun,
  FileMeta,
  MeshPreflight,
} from "@/services/api/searchExport";
import type { SearchHit, SearchQuery } from "@/services/api/attributeSearch";
import { collectFolderPaths, isUiHiddenKey } from "@/utils/storage/fileTree";
import { compareProjects } from "@/utils/sortProjects";

/** `key` with its last extension replaced by `suffix` (a key with none just gets it appended). The
 *  extension is looked for in the last path segment only: a dot in a folder name is not one. */
function replaceExt(key: string, suffix: string): string {
  const slash = key.lastIndexOf("/");
  const dot = key.lastIndexOf(".");
  const stem = dot > slash + 1 ? key.slice(0, dot) : key;
  return stem + suffix;
}

/** The provenance sidecar of a written file: `x/y/A.ifc` -> `x/y/A.meta.json`. */
export function metaSidecarKey(key: string): string {
  return replaceExt(key, ".meta.json");
}

/** The thumbnail sidecar of a written file: `x/y/A.ifc` -> `x/y/A.thumb.png`. */
export function thumbSidecarKey(key: string): string {
  return replaceExt(key, ".thumb.png");
}

/** True for what an export writes beside the files rather than as one. */
export function isExportSidecar(key: string): boolean {
  const base = key.slice(key.lastIndexOf("/") + 1).toLowerCase();
  return base === "_export.json" || base.endsWith(".meta.json") || base.endsWith(".thumb.png");
}

/** What the export dialog's "missing geometry" prompt says, from a preflight. `ask` is false when
 *  every hit has geometry (export at once, no prompt). */
export interface GeometryPrompt {
  ask: boolean;
  /** "101 of 245 hits have no exported design-system geometry (by project: ALPHA 97, BETA 4)" --
   *  the per-project counts are the requestable ones; `unknown` lists the rest. */
  headline: string | null;
  /** Collections whose hits the geometry provider's tree does not know, each with why -- a
   *  request by node does not help these. */
  unknown: { collection: string; count: number; reason: string | null; examples: string[] }[];
  /** Missing hits a request can fix, and the request jobs it takes. */
  requestable: number;
  jobs: number;
  /** Null when the user may queue the request, else why not. */
  cannotRequest: string | null;
  /** Set when a sweep of the geometry provider is running on the same seat. */
  sweepNote: string | null;
  /** Missing hits a request from HERE cannot fix -- they lie in a scope this user cannot publish
   *  into -- and what to do instead; null when there are none. */
  notHere: string | null;
}

export function geometryPrompt(pre: ExportPreflight): GeometryPrompt {
  const blocked = pre.collections.filter((c) => c.missing > 0 && c.request_blocked);
  const blockedHits = blocked.reduce((n, c) => n + c.missing, 0);
  const lacking = pre.missing + pre.unknown;
  const byCollection = pre.collections
    .filter((c) => c.missing > 0)
    .sort((a, b) => b.missing - a.missing)
    .map((c) => `${c.collection.toUpperCase()} ${c.missing}`)
    .join(", ");
  const unknown = pre.collections
    .filter((c) => c.unknown > 0)
    .map((c) => ({ collection: c.collection.toUpperCase(), count: c.unknown, reason: c.unknown_reason, examples: c.unknown_examples }));
  let cannotRequest: string | null = null;
  if (pre.missing > 0 && !pre.request.allowed) cannotRequest = pre.request.reason ?? "the geometry request is not available";
  return {
    ask: lacking > 0,
    headline: lacking > 0
      ? `${lacking.toLocaleString()} of ${pre.hits.toLocaleString()} hits have no exported design-system geometry` +
        (byCollection ? ` (by project: ${byCollection})` : "")
      : null,
    unknown,
    requestable: pre.missing,
    jobs: pre.jobs,
    cannotRequest,
    sweepNote: pre.sweep_running
      ? "A sweep is running on the design-system worker; requests will interleave with it and may cost extra " +
        "project switches -- consider waiting for it or pausing it."
      : null,
    notHere: blockedHits
      ? `${blockedHits.toLocaleString()} of the missing hits lie in a scope you cannot publish into ` +
        `(${[...new Set(blocked.map((c) => c.collection.toUpperCase()))].join(", ")}): a request from here skips them -- ` +
        "request their geometry from that scope"
      : null,
  };
}

/** What the export dialog asks before a MESH export, from its preflight. `ask` is false when every
 *  hit has a mesh to cut (export at once). */
export interface MeshPrompt {
  ask: boolean;
  /** "2 of 3 collections have no project tree from its provider yet -- 95 of 120 hits". */
  headline: string | null;
  /** Hits that will be written, and the collections they are in. */
  ready: number;
  readyCollections: string[];
  /** Collections with nothing to write -- what "export what is ready" leaves out. */
  emptyCollections: string[];
  /** Per provider: its collection request for the collections with no tree, or hits outside it. */
  requests: {
    provider: string;
    /** The scope the trees are requested INTO (a storage prefix); null is this scope. */
    origin: string | null;
    collections: string[];
    label: string;
    /** Null when the user may run it, else why not. */
    cannot: string | null;
    sweepNote: string | null;
  }[];
  /** Collections whose tree does not hold some hits, each with why. */
  unknown: { collection: string; count: number; reason: string | null; examples: string[] }[];
}

export function meshPrompt(pre: MeshPreflight): MeshPrompt {
  const lacking = pre.no_tree + pre.unknown;
  const noTree = pre.collections.filter((c) => c.no_tree > 0);
  const providers = [...new Set(noTree.map((c) => c.provider))];
  const byProvider = providers.length === 1 ? ` from ${providers[0]}` : "";
  const collections = new Set(pre.collections.map((c) => c.collection)).size;
  const parts: string[] = [];
  if (noTree.length) {
    const hits = noTree.reduce((n, c) => n + c.no_tree, 0);
    parts.push(
      `${noTree.length} of ${collections} collection${collections === 1 ? "" : "s"} have no project tree${byProvider} yet -- ` +
        `${hits.toLocaleString()} of ${pre.hits.toLocaleString()} hits`,
    );
  }
  if (pre.unknown) parts.push(`${pre.unknown.toLocaleString()} hit${pre.unknown === 1 ? " is" : "s are"} not in their tree`);
  const ready = pre.collections.filter((c) => c.ready > 0).map((c) => c.collection);
  return {
    ask: lacking > 0,
    headline: parts.length ? parts.join("; ") : null,
    ready: pre.ready,
    readyCollections: [...new Set(ready)],
    emptyCollections: [...new Set(pre.collections.filter((c) => c.ready === 0).map((c) => c.collection))],
    requests: pre.tree_requests.map((r) => ({
      provider: r.provider,
      origin: r.origin ?? null,
      collections: r.collections,
      label: r.label ?? `Request project trees (${r.provider})`,
      cannot: !r.available || !r.allowed ? (r.reason ?? "the request is not available") : null,
      sweepNote: r.sweep_running
        ? `A ${r.provider} sweep is running; a tree request waits until it has finished (one run per provider).`
        : null,
    })),
    unknown: pre.collections
      .filter((c) => c.unknown > 0)
      .map((c) => ({ collection: c.collection, count: c.unknown, reason: c.unknown_reason, examples: c.unknown_examples })),
  };
}

/** Item outcomes that ran to the end without writing every file: a rerun retries them only when asked. */
const FAILED_ITEM_STATES = new Set(["partial", "error"]);

/** Whether a rerun would drive anything: the run is not running and has an item that did not
 *  finish -- or, with `retryFailed`, any item not `done`. */
export function rerunnable(run: Pick<ExportRun, "status" | "counts">, retryFailed = true): boolean {
  if (run.status === "running") return false;
  return Object.entries(run.counts ?? {}).some(
    ([status, n]) => status !== "done" && (retryFailed || !FAILED_ITEM_STATES.has(status)) && (n ?? 0) > 0,
  );
}

/** "logins: 1 (adopted ×4)" from an item's request jobs: how many opened a session of their own,
 *  and how many reused the one already open. Null when there were none. */
export function loginSummary(batches: readonly ExportRequestBatch[] | undefined): string | null {
  const done = (batches ?? []).filter((b) => b.status && b.status !== "error");
  if (!done.length) return null;
  const adopted = done.filter((b) => b.login_mode === "adopted").length;
  const logins = done.length - adopted;
  return adopted ? `logins: ${logins} (adopted ×${adopted})` : `logins: ${logins}`;
}

/** A typed folder as the API takes it: trimmed, no leading or trailing slash, no empty or `.`
 *  segments. `..` is refused rather than resolved -- the server would refuse it too, and saying so
 *  here names the problem before a round trip. */
export function normalizeFolder(input: string): { ok: true; folder: string } | { ok: false; error: string } {
  const parts = input
    .trim()
    .replace(/\\/g, "/")
    .split("/")
    .map((p) => p.trim())
    .filter((p) => p && p !== ".");
  if (!parts.length) return { ok: false, error: "Name a folder to write into" };
  if (parts.some((p) => p === "..")) return { ok: false, error: "A folder cannot climb out with '..'" };
  return { ok: true, folder: parts.join("/") };
}

/** Folders to offer for the target: every folder (and parent) in the target scope's listing,
 *  minus the published-asset store the file browser hides too, those containing `typed` first
 *  (case ignored), at most `limit`. */
export function folderSuggestions(keys: readonly string[], typed = "", limit = 50): string[] {
  const all = collectFolderPaths(
    keys.filter((k) => !isUiHiddenKey(k)),
    (k) => k,
  );
  const t = typed.trim().replace(/^\/+/, "").toLowerCase();
  const hits = t ? all.filter((f) => f.toLowerCase().includes(t)) : all;
  // Prefix matches before mere containment; each group in the listing's (sorted) order.
  const ranked = t ? [...hits.filter((f) => f.toLowerCase().startsWith(t)), ...hits.filter((f) => !f.toLowerCase().startsWith(t))] : hits;
  return ranked.slice(0, limit);
}

/** A folder name for a new export that says when it was made: `exports/search-20261012-1405`. */
export function defaultExportFolder(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `exports/search-${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}`;
}

export interface ScopeChoice {
  /** Wire form: `shared`, `user:me`, `project:<id>`. */
  url: string;
  label: string;
}

/** The scopes an export may write into, from the scopes the user can see: their own, shared and
 *  their projects, own first. Corpus scopes are admin read-only material and never a target. The
 *  server has the last word (403 when a scope is not writable for this user). */
export function exportTargetScopes(
  available: readonly { kind: string; id: string | null; name: string }[],
): ScopeChoice[] {
  const rank = (k: string) => (k === "user" ? 0 : k === "shared" ? 1 : 2);
  return available
    .filter((s) => s.kind === "user" || s.kind === "shared" || (s.kind === "project" && s.id))
    .slice()
    .sort((a, b) => rank(a.kind) - rank(b.kind) || compareProjects(a.name, b.name))
    .map((s) => ({
      url: s.kind === "user" ? "user:me" : s.kind === "shared" ? "shared" : `project:${s.id}`,
      label: s.kind === "project" ? `Project · ${s.name}` : s.name,
    }));
}

/** The gallery's walk of one folder: the openable files under it (sidecars never), in the order
 *  the folder's `_export.json` lists them when there is one -- the order they were exported in,
 *  which keeps a collection's hits together -- then whatever else is there alphabetically (files
 *  the doc does not know: written by hand, or by an item that finished after it was read). Keys the
 *  doc names but the listing lacks are left out: the walk only offers what can be loaded. */
export function galleryFolderOrder(
  keys: readonly string[],
  folder: string,
  doc: Pick<ExportDoc, "files"> | null,
  canOpen: (key: string) => boolean = () => true,
): string[] {
  const prefix = folder.replace(/^\/+|\/+$/g, "");
  const under = prefix ? `${prefix}/` : "";
  const present = new Set(keys.filter((k) => k.startsWith(under) && !isExportSidecar(k) && canOpen(k)));
  const out: string[] = [];
  for (const f of doc?.files ?? []) {
    if (present.delete(f.file)) out.push(f.file);
  }
  return [...out, ...[...present].sort((a, b) => a.localeCompare(b))];
}

/** One stop of the gallery's folder walk: a file, or one hit inside a merged file. */
export interface GalleryStop {
  /** The file to have loaded. */
  file: string;
  /** Its position in the file list (what "project n / N" counts). */
  fileIndex: number;
  /** The hit inside the file, or null for a file shown whole. */
  joint: ExportJoint | null;
  /** `joint`'s position in its file, and how many the file has (0 / 0 for a whole file). */
  jointIndex: number;
  joints: number;
  /** The thumbnail to show: a key, null for none, undefined to look beside the file. */
  thumb: string | null | undefined;
  /** Its project (collection) as the doc lists it; null for a file the doc does not list. */
  project: string | null;
  /** A stable id for React lists. */
  id: string;
}

/** The folder walk's stops: each file of `files` once -- except a merged file the doc lists its
 *  hits for, which is one stop per hit, with that hit's own thumbnail. So stepping goes through
 *  the hits of a collection and then on to the next collection. */
export function galleryStops(files: readonly string[], doc: Pick<ExportDoc, "files"> | null): GalleryStop[] {
  const byFile = new Map((doc?.files ?? []).map((f) => [f.file, f] as const));
  const out: GalleryStop[] = [];
  files.forEach((file, fileIndex) => {
    const entry = byFile.get(file);
    const joints = entry?.joints ?? [];
    const project = entry?.collection ?? null;
    if (!joints.length) {
      out.push({ file, fileIndex, joint: null, jointIndex: 0, joints: 0, thumb: entry ? entry.thumb : undefined, project, id: file });
      return;
    }
    joints.forEach((joint, jointIndex) =>
      out.push({ file, fileIndex, joint, jointIndex, joints: joints.length, thumb: joint.thumb, project, id: `${file}#${jointIndex}` }),
    );
  });
  return out;
}

/** The gallery's pages: each run of stops of one project, `[lo, hi)`, in walk order. */
export function projectPages(stops: readonly GalleryStop[]): { project: string | null; lo: number; hi: number }[] {
  const pages: { project: string | null; lo: number; hi: number }[] = [];
  stops.forEach((s, i) => {
    const last = pages[pages.length - 1];
    if (last && last.project === s.project) last.hi = i + 1;
    else pages.push({ project: s.project, lo: i, hi: i + 1 });
  });
  return pages;
}

/** The first stop of the page `delta` pages from the one holding `at` -- the "next project" step.
 *  Wraps around. With a single page it steps files instead (`stopOfAdjacentFile`). */
export function stopOfAdjacentProject(stops: readonly GalleryStop[], at: number, delta: 1 | -1): number {
  const pages = projectPages(stops);
  if (pages.length < 2) return stopOfAdjacentFile(stops, at, delta);
  const page = Math.max(0, pages.findIndex((p) => at >= p.lo && at < p.hi));
  return pages[(page + delta + pages.length) % pages.length].lo;
}

/** The stop `delta` files away from `at`, landing on the next file's FIRST hit (or the previous
 *  file's first, going back). Wraps around. */
export function stopOfAdjacentFile(stops: readonly GalleryStop[], at: number, delta: 1 | -1): number {
  if (!stops.length) return 0;
  const files = stops[stops.length - 1].fileIndex + 1;
  const want = (((stops[at]?.fileIndex ?? 0) + delta) % files + files) % files;
  const i = stops.findIndex((s) => s.fileIndex === want);
  return i < 0 ? at : i;
}

/** `[lo, hi)` of a `size`-wide window around `index`, shifted inward at the ends so it keeps its
 *  size while the list is long enough. */
export function stripWindow(length: number, index: number, size: number): [number, number] {
  const n = Math.min(size, length);
  const lo = Math.max(0, Math.min(index - Math.floor(n / 2), length - n));
  return [lo, lo + n];
}

/** The projects (collections) of a folder walk, in export order, with how many files each has. */
export function galleryProjects(files: readonly string[], doc: Pick<ExportDoc, "files"> | null): { name: string; files: number }[] {
  const byFile = new Map((doc?.files ?? []).map((f) => [f.file, f.collection] as const));
  const counts = new Map<string, number>();
  for (const f of files) {
    const c = byFile.get(f);
    if (c) counts.set(c, (counts.get(c) ?? 0) + 1);
  }
  return [...counts].map(([name, n]) => ({ name, files: n }));
}

/** `files` without those of the `hidden` projects; files the doc does not list are kept. */
export function filterByProjects(
  files: readonly string[],
  doc: Pick<ExportDoc, "files"> | null,
  hidden: ReadonlySet<string>,
): string[] {
  if (!hidden.size) return [...files];
  const byFile = new Map((doc?.files ?? []).map((f) => [f.file, f.collection] as const));
  return files.filter((f) => !hidden.has(byFile.get(f) ?? ""));
}

/** A search hit for one hit of a merged file, to find it in the source tree; null when the export
 *  predates per-hit paths or the file's provenance does not name its provider and collection. */
export function hitFromJoint(joint: ExportJoint, meta: FileMeta | null): SearchHit | null {
  const s = meta?.source;
  if (!s?.provider || !s.collection || !joint.node_id || !joint.path?.length) return null;
  const parent = joint.path[joint.path.length - 1];
  return {
    provider: s.provider,
    collection: s.collection,
    node_id: joint.node_id,
    parent_id: parent?.node_id ?? null,
    full_name: joint.full_name ?? joint.name,
    source_ref: null,
    kind: null,
    depth: null,
    attrs: {},
    path: joint.path,
  };
}

/** The folders an export index for `key` may sit in, nearest first: an export writes per-hit files
 *  one level below its folder and merged files directly in it. */
export function exportFolderCandidates(key: string, depth = 3): string[] {
  const parts = key.split("/").slice(0, -1);
  const out: string[] = [];
  for (let n = parts.length; n > 0 && out.length < depth; n--) out.push(parts.slice(0, n).join("/"));
  return out;
}

/** A one-file export doc from a merged file's own sidecar, for walking its hits when no folder
 *  index lists it; null when the sidecar names no hits. */
export function docFromMeta(file: string, meta: FileMeta | null): Pick<ExportDoc, "files"> | null {
  const joints = meta?.joints ?? [];
  if (!joints.length) return null;
  const s = meta?.source;
  return {
    files: [
      {
        file,
        collection: s?.collection ?? "",
        provider: s?.provider ?? "",
        node_id: s?.node_id ?? "",
        full_name: s?.full_name ?? "",
        meta: metaSidecarKey(file),
        thumb: meta?.derived?.thumb ?? null,
        hits: joints.length,
        joints,
      },
    ],
  };
}

/** A tree node a hit is found by when its file does not give its ids: same name, ignoring a
 *  leading `/`, surrounding space and, failing that, case. */
export function jointNameMatches(nodeName: string, joint: Pick<ExportJoint, "name" | "full_name">): boolean {
  const norm = (s: string) => s.trim().replace(/^\/+/, "").trim();
  const n = norm(nodeName);
  const wanted = [joint.name, joint.full_name].filter((w): w is string => !!w).map(norm);
  return wanted.includes(n) || wanted.some((w) => w.toLowerCase() === n.toLowerCase());
}

/** A hit-like object built from a file's provenance, for "Show in source tree"; null when the
 *  provenance lacks what a reveal walks by (provider, collection, node). */
export function hitFromMeta(meta: FileMeta | null): SearchHit | null {
  const s = meta?.source;
  if (!s || !s.provider || !s.collection || !s.node_id) return null;
  const path = s.path ?? [];
  const last = path[path.length - 1];
  const parent = last && last.node_id === s.node_id ? path[path.length - 2] : last;
  return {
    provider: s.provider,
    collection: s.collection,
    node_id: s.node_id,
    parent_id: parent?.node_id ?? null,
    full_name: s.full_name ?? s.node_id,
    source_ref: s.source_ref ?? null,
    kind: last && last.node_id === s.node_id ? last.kind : null,
    depth: last && last.node_id === s.node_id ? last.depth : null,
    attrs: {},
    path,
  };
}

/** A query in one line, for a card: `collections A, B · kind X · KEY = v · KEY2 in [a, b]`. */
export function compactQuery(q: SearchQuery | null | undefined): string {
  if (!q) return "";
  const parts: string[] = [];
  if (q.collections?.length) parts.push(`collections ${q.collections.join(", ")}`);
  if (q.exclude_collections?.length) parts.push(`not collections ${q.exclude_collections.join(", ")}`);
  if (q.providers?.length) parts.push(`providers ${q.providers.join(", ")}`);
  if (q.exclude_providers?.length) parts.push(`not providers ${q.exclude_providers.join(", ")}`);
  if (q.kind?.length) parts.push(`kind ${q.kind.join(", ")}`);
  if (q.depth) {
    if ("eq" in q.depth) parts.push(`depth ${q.depth.eq}`);
    else parts.push(`depth ${q.depth.min ?? ""}..${q.depth.max ?? ""}`);
  }
  for (const w of q.where ?? []) {
    if ("eq" in w) parts.push(`${w.key} = ${String(w.eq)}`);
    else if ("ne" in w) parts.push(`${w.key} != ${String(w.ne)}`);
    else if ("in" in w) parts.push(`${w.key} in [${w.in.map(String).join(", ")}]`);
    else if ("not_in" in w) parts.push(`${w.key} not in [${w.not_in.map(String).join(", ")}]`);
    else if ("exists" in w) parts.push(`has ${w.key}`);
    else if ("missing" in w) parts.push(`no ${w.key}`);
    else parts.push(`${w.key} starts ${w.prefix}`);
  }
  return parts.join(" · ") || "everything";
}

/** The query an export of "all matches" sends: the search's own, with the collections the user
 *  deselected (by clicking their count) added to its exclusions -- so the server pages through
 *  exactly what is left, and its cap counts that. An include list loses them too, so a query that
 *  named only deselected collections names none rather than contradicting itself. */
export function exportQuery(query: SearchQuery, deselected: ReadonlySet<string>): SearchQuery {
  if (!deselected.size) return query;
  // Collections compare case-insensitively on the server; the include list is filtered the same way.
  const out = new Set([...deselected].map((c) => c.toLowerCase()));
  const exclude = [...new Set([...(query.exclude_collections ?? []), ...deselected])];
  const include = query.collections?.filter((c) => !out.has(c.toLowerCase())) ?? null;
  return { ...query, collections: include, exclude_collections: exclude };
}

// --- the preflight, a collection at a time -----------------------------------------------------------
//
// One preflight of a whole search pages through every hit and reads every collection's published
// tree before it answers, which on a search across dozens of collections is a long wait with nothing
// to show. Checked a collection at a time, a few at once, it reports as it goes -- and the answers
// add up to exactly the one big check's: every count is per collection to begin with.

/** `query` narrowed to one collection (its include list, if any, already holds it). */
export function queryForCollection(query: SearchQuery, collection: string): SearchQuery {
  return { ...query, collections: [collection] };
}

/** `query` narrowed to one partition, by key (`partitionKey`): one origin's collection, where a
 *  name would take every scope's of that name. An include list the query had is replaced -- it
 *  held this one -- and its exclusions stay. */
export function queryForPartition(query: SearchQuery, key: string): SearchQuery {
  return { ...query, partitions: [key] };
}

/** The query an export of "all matches" sends when the panel left partitions out (by clicking
 *  their counts): they join its `exclude_partitions`, and an include list loses them -- as
 *  `exportQuery` does for names. */
export function exportQueryParts(query: SearchQuery, deselected: ReadonlySet<string>): SearchQuery {
  if (!deselected.size) return query;
  const exclude = [...new Set([...(query.exclude_partitions ?? []), ...deselected])];
  const include = query.partitions ? query.partitions.filter((k) => !deselected.has(k)) : undefined;
  return { ...query, ...(include ? { partitions: include } : {}), exclude_partitions: exclude };
}

/** Run `fn` over `items`, at most `width` at a time, calling `onDone` after each. Results in order. */
export async function mapLimited<T, R>(
  items: readonly T[],
  width: number,
  fn: (item: T) => Promise<R>,
  onDone?: (done: number) => void,
): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  let done = 0;
  const lane = async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      out[i] = await fn(items[i]);
      done += 1;
      onDone?.(done);
    }
  };
  await Promise.all(Array.from({ length: Math.max(1, Math.min(width, items.length)) }, lane));
  return out;
}

/** Per-collection design preflights as the one preflight of all of them. */
export function mergePreflights(parts: readonly ExportPreflight[]): ExportPreflight {
  const [first] = parts;
  const sum = (k: "hits" | "ready" | "missing" | "unknown" | "jobs") => parts.reduce((n, p) => n + p[k], 0);
  return {
    ...first,
    hits: sum("hits"),
    ready: sum("ready"),
    missing: sum("missing"),
    unknown: sum("unknown"),
    jobs: sum("jobs"),
    collections: parts.flatMap((p) => p.collections),
    sweep_running: parts.find((p) => p.sweep_running)?.sweep_running ?? null,
    took_ms: parts.reduce((n, p) => Math.max(n, p.took_ms), 0),
  };
}

/** Per-collection mesh preflights as the one preflight of all of them: each provider's tree request
 *  -- one per scope the trees go into -- covers every collection any part found without a tree. */
export function mergeMeshPreflights(parts: readonly MeshPreflight[]): MeshPreflight {
  const sum = (k: "hits" | "ready" | "no_tree" | "unknown") => parts.reduce((n, p) => n + p[k], 0);
  const requests = new Map<string, MeshPreflight["tree_requests"][number]>();
  for (const r of parts.flatMap((p) => p.tree_requests)) {
    const key = `${r.provider}\u0000${r.origin ?? ""}`;
    const seen = requests.get(key);
    requests.set(
      key,
      seen
        ? { ...seen, collections: [...new Set([...seen.collections, ...r.collections])], sweep_running: seen.sweep_running ?? r.sweep_running }
        : { ...r },
    );
  }
  return {
    geometry: "mesh",
    hits: sum("hits"),
    ready: sum("ready"),
    no_tree: sum("no_tree"),
    unknown: sum("unknown"),
    collections: parts.flatMap((p) => p.collections),
    tree_requests: [...requests.values()],
    took_ms: parts.reduce((n, p) => Math.max(n, p.took_ms), 0),
  };
}

/** The matches left once the `deselected` partitions (by `counts` key) are out: the sum of the
 *  others -- own and other scopes' alike, since an export takes them all. */
export function selectedTotal(
  counts: readonly { key: string; count: number }[],
  deselected: ReadonlySet<string>,
): number {
  return counts.reduce((n, c) => (deselected.has(c.key) ? n : n + c.count), 0);
}
