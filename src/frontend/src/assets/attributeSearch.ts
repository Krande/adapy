// Attribute search, the pure half: what the search panel's form turns into on the wire, how pages
// of hits fold into one result, and how a hit finds its row in the Sources tree.
//
// THE FORM IS TEXT, THE QUERY IS TYPED. A filter row holds what the user typed; the index holds
// strings, numbers and booleans as the provider wrote them, and an equality match is a jsonb
// containment, where `"12"` and `12` are different values. So a value is sent as the user typed it
// (a string) unless it was PICKED from the server's own suggestions, whose typed value is then
// remembered on the row and sent instead. Nothing is guessed from what a string looks like.
//
// PROVIDER-NEUTRAL. Keys, kinds and values are whatever the provider wrote; no key is special here
// and no default names one. A key the user wants (a type, a function code) comes from the keys
// the server lists or from what the user typed.
//
// A HIT REVEALS BY ID, AND BY NAME WHERE THE ID IS NOT HELD. Each hit carries its path root first,
// every entry with the id the provider's published tree uses (`node_id`). The walk opens the tree
// one level at a time down that path. A path entry the tree does not hold (the index can name a
// level above the tree's top, or a level the tree draws under another id) is matched by name
// among the children of the last row reached -- the same matching a 3D pick uses
// (`@/assets/scenePick`) -- and skipped when nothing matches. The walk stops at the deepest row it
// reached: a reveal of the nearest ancestor is still the right place to look, an error is not.
//
// A SHARED HIT IS ANOTHER SCOPE'S. A partition an admin shared into this scope is searched here
// (and, for an admin who asks, every provider scope's: `all_scopes`), but its tree and its geometry
// live in the scope it came from (`scope` on the hit). Its identity includes that origin -- the same
// node id in an own and a shared partition of one name are two hits. Revealing one switches to its
// origin first (`hitRevealScope`), as revealing an exported file does: this scope's tree is not
// where that node is. An export from here takes it: the server reads it at its origin and writes
// the files where the export says.
//
// A PARTITION IS PICKED BY ITS KEY (`partitionKey`, the server's `counts` key), not by its
// collection's name: two scopes' partitions of one collection are two options, so one can be left
// out and the other kept. What is left out is remembered per scope (`withScopePicks`), so a scope's
// "not now" list survives a reload; a form stored when picks were names is read as before.
//
// React-free and store-free, so it runs under `node --test`.

import { matchRow, type PickRow, type PickTreeSource } from "@/assets/scenePick";
import type {
  SearchDepth,
  SearchHit,
  SearchOrigin,
  SearchPartition,
  SearchPathEntry,
  SearchQuery,
  SearchResponse,
  SearchWhere,
} from "@/services/api/attributeSearch";
import { compareProjects } from "@/utils/sortProjects";

export type FilterOp = "eq" | "ne" | "in" | "not_in" | "prefix" | "exists" | "missing";

// The negative operators are the exact complement of their positive twin: "not equal" matches a
// node whose value differs OR that has no such attribute at all (add "has key" to rule those out).
export const FILTER_OPS: readonly { id: FilterOp; label: string; title: string }[] = [
  { id: "eq", label: "equals", title: "The attribute has exactly this value" },
  {
    id: "ne",
    label: "not equal",
    title: "The attribute has another value, or the node does not have this attribute at all",
  },
  { id: "in", label: "any of", title: "The attribute has one of these values (comma separated)" },
  {
    id: "not_in",
    label: "not any of",
    title: "The attribute has none of these values (comma separated), or the node does not have it at all",
  },
  { id: "prefix", label: "starts with", title: "The attribute's value starts with this text" },
  { id: "exists", label: "has key", title: "The node has this attribute, whatever its value" },
  { id: "missing", label: "lacks key", title: "The node does not have this attribute" },
];

const OP_IDS: readonly string[] = FILTER_OPS.map((o) => o.id);

/** Whether `op` takes a comma-separated list of values. */
export function opTakesList(op: FilterOp): boolean {
  return op === "in" || op === "not_in";
}

/** Whether `op` takes a value at all ("has key" / "lacks key" do not). */
export function opTakesValue(op: FilterOp): boolean {
  return op !== "exists" && op !== "missing";
}

/** How a provider / collection choice applies: only the chosen ones, or every one but them. Either
 *  way, nothing chosen means every one -- so excluding one of ninety-five is one tick, not 94. */
export type PickMode = "include" | "exclude";

export type ScalarValue = string | number | boolean;

/** One filter row as the panel holds it. `typed`: values picked from the server's suggestions,
 *  by the text shown for them, so they are sent as the type the index holds them in. */
export interface FilterRow {
  id: string;
  key: string;
  op: FilterOp;
  value: string;
  typed?: Record<string, ScalarValue>;
}

export type DepthMode = "any" | "eq" | "range";

/** Everything the panel's form holds -- what is remembered per browser as "the last query". */
export interface SearchForm {
  /** Collection NAMES chosen; empty = every ingested collection. What a form stored before
   *  partition picks holds -- the picker turns them into `partitions` once it knows them
   *  (`partitionsOfNames`) and leaves this empty. */
  collections: string[];
  /** Whether `collections` are the ones searched or the ones left out. */
  collectionMode: PickMode;
  /** Partitions chosen, by `partitionKey`; empty = every one. */
  partitions: string[];
  partitionMode: PickMode;
  /** An admin's search of every provider scope (`all_scopes`). */
  allScopes: boolean;
  /** Provider ids chosen; empty = every provider. */
  providers: string[];
  providerMode: PickMode;
  /** Kinds, comma separated; blank = any kind. */
  kind: string;
  depthMode: DepthMode;
  depthEq: string;
  depthMin: string;
  depthMax: string;
  rows: FilterRow[];
  /** Attribute keys shown as result columns; also what the search asks the server to return. */
  columns: string[];
  limit: number;
}

export const DEFAULT_LIMIT = 200;
export const MAX_LIMIT = 1000;

let rowSeq = 0;
export function newFilterRow(key = ""): FilterRow {
  rowSeq += 1;
  return { id: `f${Date.now().toString(36)}${rowSeq}`, key, op: "eq", value: "" };
}

export function emptyForm(): SearchForm {
  return {
    collections: [],
    collectionMode: "include",
    partitions: [],
    partitionMode: "include",
    allScopes: false,
    providers: [],
    providerMode: "include",
    kind: "",
    depthMode: "any",
    depthEq: "",
    depthMin: "",
    depthMax: "",
    rows: [newFilterRow()],
    columns: [],
    limit: DEFAULT_LIMIT,
  };
}

/** Comma-separated text -> trimmed, non-empty, de-duplicated items, in order. */
export function splitList(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(",")) {
    const t = part.trim();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

/** The value to send for `text` on `row`: the suggestion's typed value when one was picked under
 *  exactly this text, else the text. */
export function typedValue(row: Pick<FilterRow, "typed">, text: string): ScalarValue {
  const t = row.typed;
  return t && Object.prototype.hasOwnProperty.call(t, text) ? t[text] : text;
}

function intOrNull(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) ? n : NaN;
}

export type BuildResult = { ok: true; query: SearchQuery } | { ok: false; error: string };

/** The wire query for `form`, page `cursor`. A row with no key is ignored (a blank row the user
 *  has not filled yet); a row with a key and no value is an error rather than a silent no-op, since
 *  dropping it would widen the search past what the user asked for. */
export function buildQuery(form: SearchForm, cursor: string | null = null): BuildResult {
  const where: SearchWhere[] = [];
  for (const row of form.rows) {
    const key = row.key.trim();
    if (!key) continue;
    if (row.op === "exists" || row.op === "missing") {
      where.push(row.op === "exists" ? { key, exists: true } : { key, missing: true });
    } else if (opTakesList(row.op)) {
      const items = splitList(row.value);
      if (!items.length) return { ok: false, error: `"${key}": give at least one value` };
      const values = items.map((v) => typedValue(row, v));
      where.push(row.op === "in" ? { key, in: values } : { key, not_in: values });
    } else if (row.op === "prefix") {
      const p = row.value.trim();
      if (!p) return { ok: false, error: `"${key}": give the start of the value` };
      where.push({ key, prefix: p });
    } else {
      const v = row.value.trim();
      if (!v) return { ok: false, error: `"${key}": give a value` };
      where.push(row.op === "ne" ? { key, ne: typedValue(row, v) } : { key, eq: typedValue(row, v) });
    }
  }

  let depth: SearchDepth | null = null;
  if (form.depthMode === "eq") {
    const eq = intOrNull(form.depthEq);
    if (eq === null || Number.isNaN(eq)) return { ok: false, error: "Depth: give a whole number" };
    depth = { eq };
  } else if (form.depthMode === "range") {
    const min = intOrNull(form.depthMin);
    const max = intOrNull(form.depthMax);
    if (Number.isNaN(min) || Number.isNaN(max)) return { ok: false, error: "Depth: min and max must be whole numbers" };
    if (min !== null && max !== null && min > max) return { ok: false, error: "Depth: min is above max" };
    if (min !== null || max !== null) {
      depth = {};
      if (min !== null) depth.min = min;
      if (max !== null) depth.max = max;
    }
  }

  const kinds = splitList(form.kind);
  const limit = Math.max(1, Math.min(MAX_LIMIT, Math.floor(form.limit) || DEFAULT_LIMIT));
  const picked = (names: readonly string[], mode: PickMode, want: PickMode) =>
    names.length && mode === want ? [...names] : null;
  return {
    ok: true,
    query: {
      providers: picked(form.providers, form.providerMode, "include"),
      collections: picked(form.collections, form.collectionMode, "include"),
      exclude_providers: picked(form.providers, form.providerMode, "exclude"),
      exclude_collections: picked(form.collections, form.collectionMode, "exclude"),
      // Sent only when set, so a query without them is the one an older server takes.
      ...(form.partitions.length
        ? form.partitionMode === "exclude"
          ? { exclude_partitions: [...form.partitions] }
          : { partitions: [...form.partitions] }
        : {}),
      ...(form.allScopes ? { all_scopes: true } : {}),
      kind: kinds.length ? kinds : null,
      depth,
      where,
      // No columns chosen: every attribute, so a hit's detail (and an export) has them all.
      select: form.columns.length ? [...form.columns] : null,
      limit,
      cursor,
    },
  };
}

/** The options whose value or label contains `text`, ignoring case -- the type-ahead of a
 *  provider / collection pick. Blank text keeps them all. */
export function filterOptions<T extends { value: string; label: string }>(options: readonly T[], text: string): T[] {
  const t = text.trim().toLowerCase();
  if (!t) return [...options];
  return options.filter((o) => o.value.toLowerCase().includes(t) || o.label.toLowerCase().includes(t));
}

/** A pick in a few words: `every collection`, `ALPHA`, `3 of them`, `all but ALPHA`, `all but 2`. */
export function pickSummary(
  chosen: readonly string[],
  mode: PickMode,
  noun: string,
  labelOf: (value: string) => string = (v) => v,
): string {
  if (!chosen.length) return `every ${noun}`;
  const what = chosen.length === 1 ? labelOf(chosen[0]) : `${chosen.length}`;
  if (mode === "exclude") return `all but ${what}`;
  return chosen.length === 1 ? what : `${what} chosen`;
}

/** A hit's identity across pages and partitions -- its origin scope included, so an own and a
 *  shared partition of one name never merge. A hit with no `scope` (an older server) is keyed as
 *  it always was. */
export function hitKey(hit: Pick<SearchHit, "provider" | "collection" | "node_id" | "scope">): string {
  const own = `${hit.provider}\u0000${hit.collection}\u0000${hit.node_id}`;
  return hit.scope ? `${hit.scope}\u0000${own}` : own;
}

/** Whether a hit / collection / partition was read through a share from another scope. */
export function isShared(origin: SearchOrigin): boolean {
  return origin.shared === true;
}

/** A short name for an origin scope prefix, as the scope picker names scopes: "Shared", "My
 *  files", a project's name (from `projectName`, else "project"), or the prefix itself. */
export function originLabel(scope: string | undefined, projectName: (id: string) => string | undefined = () => undefined): string {
  if (!scope) return "";
  if (scope === "shared") return "Shared";
  if (scope.startsWith("users/")) return "My files";
  if (scope.startsWith("projects/")) return projectName(scope.slice("projects/".length)) ?? "project";
  return scope;
}

/** The badge for a shared origin ("from Shared", "from <project>"), null for the scope's own. */
export function originBadge(origin: SearchOrigin, projectName?: (id: string) => string | undefined): string | null {
  return isShared(origin) ? `from ${originLabel(origin.scope, projectName)}` : null;
}

/** The scope to reveal a hit in (its path form, for `revealInSourceScope`): null for the scope's
 *  own hits (reveal here), the origin's for a shared one -- or `blocked` with the reason when the
 *  origin has no path form this caller can use. */
export function hitRevealScope(
  hit: SearchOrigin,
  projectName?: (id: string) => string | undefined,
): { scope: string | null } | { blocked: string } {
  if (!isShared(hit)) return { scope: null };
  if (hit.scope_wire) return { scope: hit.scope_wire };
  return { blocked: `Shared into this scope from ${originLabel(hit.scope, projectName)}, which cannot be opened from here` };
}

/** What an export from this scope does with a hit of another scope's, for its tooltip; null for
 *  the scope's own. */
export function hitExportNote(hit: SearchOrigin, projectName?: (id: string) => string | undefined): string | null {
  if (!isShared(hit)) return null;
  const via = hit.admin ? "Read through your admin search of every scope" : "Shared into this scope";
  return `${via} from ${originLabel(hit.scope, projectName)}: an export reads its geometry and attributes there`;
}

/** A partition's key -- the server's `counts` key, what `partitions` / `exclude_partitions` name:
 *  `<provider>/<collection>` for the scope's own, `...@<origin>` for another scope's. The server's
 *  `key` when it sent one. */
export function partitionKey(p: { key?: string; provider: string; collection: string } & SearchOrigin): string {
  if (p.key) return p.key;
  const own = `${p.provider}/${p.collection}`;
  return isShared(p) && p.scope ? `${own}@${p.scope}` : own;
}

/** The partition keys of every collection named in `names` (a pick stored by name), in listing
 *  order -- every origin of each name, as a pick by name always took. */
export function partitionsOfNames(
  names: readonly string[],
  collections: readonly ({ key?: string; provider: string; collection: string } & SearchOrigin)[],
): string[] {
  const wanted = new Set(names.map((n) => n.toLowerCase()));
  return [...new Set(collections.filter((c) => wanted.has(c.collection.toLowerCase())).map(partitionKey))];
}

/** One scope's remembered partition pick. */
export interface ScopePicks {
  keys: string[];
  mode: PickMode;
}

/** The stored picks of `scope` (from the per-browser map's JSON), or null when it has none. */
export function readScopePicks(raw: string | null, scope: string): ScopePicks | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  const entry = v && typeof v === "object" ? (v as Record<string, unknown>)[scope] : null;
  if (!entry || typeof entry !== "object") return null;
  const e = entry as Record<string, unknown>;
  const keys = Array.isArray(e.keys) ? e.keys.filter((k): k is string => typeof k === "string" && !!k) : [];
  return { keys, mode: e.mode === "exclude" ? "exclude" : "include" };
}

/** The map's JSON with `scope`'s picks replaced -- removed when nothing is picked, so a scope left
 *  on "every partition" leaves nothing behind. A malformed map starts afresh. */
export function withScopePicks(raw: string | null, scope: string, picks: ScopePicks): string {
  let map: Record<string, unknown> = {};
  try {
    const v = raw ? JSON.parse(raw) : {};
    if (v && typeof v === "object" && !Array.isArray(v)) map = { ...(v as Record<string, unknown>) };
  } catch {
    map = {};
  }
  if (picks.keys.length) map[scope] = { keys: [...new Set(picks.keys)], mode: picks.mode };
  else delete map[scope];
  return JSON.stringify(map);
}

/** Every page fetched so far for one query, folded. */
export interface SearchResults {
  hits: SearchHit[];
  nextCursor: string | null;
  /** Totals per partition for the whole query, as the latest page reported them. */
  counts: Record<string, number>;
  /** The same, spelled out with each partition's origin (absent from an older server). */
  partitions?: SearchPartition[];
  /** The latest page's server time, and every page's summed. */
  tookMs: number;
  totalTookMs: number;
  pages: number;
}

/** Fold `page` into `prev` (null = this is the first page). A hit already held -- a cursor that
 *  overlapped, a re-sent page -- is not added twice; the first copy keeps its place. */
export function mergePage(prev: SearchResults | null, page: SearchResponse): SearchResults {
  const hits = prev ? [...prev.hits] : [];
  const seen = new Set(hits.map(hitKey));
  for (const row of page.rows) {
    const k = hitKey(row);
    if (seen.has(k)) continue;
    seen.add(k);
    hits.push(row);
  }
  return {
    hits,
    nextCursor: page.next_cursor ?? null,
    counts: { ...(page.counts ?? {}) },
    ...(page.partitions ? { partitions: [...page.partitions] } : {}),
    tookMs: page.took_ms ?? 0,
    totalTookMs: (prev?.totalTookMs ?? 0) + (page.took_ms ?? 0),
    pages: (prev?.pages ?? 0) + 1,
  };
}

export interface PartitionCount extends SearchOrigin {
  /** The `counts` key -- unique per partition, shared ones included. */
  key: string;
  provider: string;
  collection: string;
  count: number;
}

/** `counts` as rows, largest first. From `partitions` when the server sent them (each with its
 *  origin); else -- an older server, which shares nothing -- from the keys, `<provider>/<collection>`:
 *  a provider id carries no `/`, so the first one splits it (a collection name may carry one). */
export function partitionCounts(counts: Record<string, number>, partitions?: readonly SearchPartition[]): PartitionCount[] {
  const rows: PartitionCount[] = partitions
    ? partitions.map((p) => ({ ...p, count: counts[p.key] ?? p.count }))
    : Object.entries(counts).map(([key, count]) => {
        const i = key.indexOf("/");
        return i < 0
          ? { key, provider: "", collection: key, count }
          : { key, provider: key.slice(0, i), collection: key.slice(i + 1), count };
      });
  return rows.sort((a, b) => b.count - a.count || compareProjects(a.collection, b.collection) || a.key.localeCompare(b.key));
}

export function totalCount(counts: Record<string, number>): number {
  return Object.values(counts).reduce((n, c) => n + c, 0);
}

/** The tree's name for the collection a hit names. The index keeps the provider's spelling; the
 *  Sources tree keys collections in lower case. Exact first, then ignoring case; null = the tree
 *  has no such collection in this scope. */
export function treeCollectionFor(collections: readonly string[] | null, name: string): string | null {
  if (!collections) return null;
  if (collections.includes(name)) return name;
  const lower = name.toLowerCase();
  return collections.find((c) => c.toLowerCase() === lower) ?? null;
}

/** The rows to walk for a hit: its path root first, then the hit itself (once, even when the
 *  server's path already ends at it). */
export function hitTreePath(hit: SearchHit): SearchPathEntry[] {
  const path = hit.path ?? [];
  const last = path[path.length - 1];
  if (last && last.node_id === hit.node_id) return [...path];
  return [...path, { node_id: hit.node_id, full_name: hit.full_name, kind: hit.kind, depth: hit.depth }];
}

/** The last segment of a `/`-separated full name -- what a tree row is often labelled with. */
export function shortName(fullName: string): string {
  const t = fullName.trim().replace(/\/+$/, "");
  const i = t.lastIndexOf("/");
  return i >= 0 && i < t.length - 1 ? t.slice(i + 1) : t;
}

export interface HitReveal {
  /** The deepest row reached -- the one to select. */
  row: string;
  /** Rows reached, top down, `row` last. */
  chain: string[];
  /** Whether `row` is the hit itself, or an ancestor the walk stopped at. */
  exact: boolean;
}

/** Walk the tree down `path` (root first, the target last). See the module comment. */
export async function walkHitPath(src: PickTreeSource, path: readonly SearchPathEntry[]): Promise<HitReveal | null> {
  const held = (id: string) => src.label(id) !== undefined;
  const chain: string[] = [];
  let last: string | null = null;
  let exact = false;
  for (const [i, entry] of path.entries()) {
    let id: string | null = held(entry.node_id) ? entry.node_id : null;
    if (!id && last) {
      await src.ensureChildren(last);
      if (src.alive && !src.alive()) return null;
      if (held(entry.node_id)) {
        id = entry.node_id;
      } else {
        const rows: PickRow[] = [];
        for (const c of src.children(last)) {
          const label = src.label(c);
          if (label !== undefined) rows.push({ id: c, label });
        }
        id = (matchRow(rows, entry.full_name) ?? matchRow(rows, shortName(entry.full_name)))?.id ?? null;
      }
    }
    if (!id) continue;
    chain.push(id);
    last = id;
    exact = i === path.length - 1;
  }
  if (!last) return null;
  return { row: last, chain, exact };
}

/** Column keys worth offering for the hits held: the union of their attribute keys, sorted. */
export function attrKeysOf(hits: readonly SearchHit[]): string[] {
  const keys = new Set<string>();
  for (const h of hits) for (const k of Object.keys(h.attrs ?? {})) keys.add(k);
  return [...keys].sort((a, b) => a.localeCompare(b));
}

export function formatAttr(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** A form read back from storage, or null when it is not one (an older shape, a hand edit). */
export function parseStoredForm(raw: string | null): SearchForm | null {
  if (!raw) return null;
  let v: unknown;
  try {
    v = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  const strs = (x: unknown) => (Array.isArray(x) ? x.filter((s): s is string => typeof s === "string") : []);
  const str = (x: unknown) => (typeof x === "string" ? x : "");
  const base = emptyForm();
  const rows = Array.isArray(o.rows)
    ? o.rows.flatMap((r): FilterRow[] => {
        if (!r || typeof r !== "object") return [];
        const x = r as Record<string, unknown>;
        const op: FilterOp = typeof x.op === "string" && OP_IDS.includes(x.op) ? (x.op as FilterOp) : "eq";
        const typed: Record<string, ScalarValue> = {};
        if (x.typed && typeof x.typed === "object") {
          for (const [k, tv] of Object.entries(x.typed as Record<string, unknown>)) {
            if (typeof tv === "string" || typeof tv === "number" || typeof tv === "boolean") typed[k] = tv;
          }
        }
        return [{ ...newFilterRow(str(x.key)), op, value: str(x.value), ...(Object.keys(typed).length ? { typed } : {}) }];
      })
    : base.rows;
  const depthMode: DepthMode = o.depthMode === "eq" || o.depthMode === "range" ? o.depthMode : "any";
  const limit = typeof o.limit === "number" && o.limit > 0 ? Math.min(MAX_LIMIT, Math.floor(o.limit)) : DEFAULT_LIMIT;
  // A form stored before exclusions existed has no modes: its picks were the ones searched.
  const mode = (x: unknown): PickMode => (x === "exclude" ? "exclude" : "include");
  return {
    collections: strs(o.collections),
    collectionMode: mode(o.collectionMode),
    partitions: strs(o.partitions),
    partitionMode: mode(o.partitionMode),
    allScopes: o.allScopes === true,
    providers: strs(o.providers),
    providerMode: mode(o.providerMode),
    kind: str(o.kind),
    depthMode,
    depthEq: str(o.depthEq),
    depthMin: str(o.depthMin),
    depthMax: str(o.depthMax),
    rows: rows.length ? rows : base.rows,
    columns: strs(o.columns),
    limit,
  };
}
