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
// React-free and store-free, so it runs under `node --test`.

import { matchRow, type PickRow, type PickTreeSource } from "@/assets/scenePick";
import type {
  SearchDepth,
  SearchHit,
  SearchPathEntry,
  SearchQuery,
  SearchResponse,
  SearchWhere,
} from "@/services/api/attributeSearch";

export type FilterOp = "eq" | "in" | "prefix";

export const FILTER_OPS: readonly { id: FilterOp; label: string; title: string }[] = [
  { id: "eq", label: "equals", title: "The attribute has exactly this value" },
  { id: "in", label: "any of", title: "The attribute has one of these values (comma separated)" },
  { id: "prefix", label: "starts with", title: "The attribute's value starts with this text" },
];

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
  /** Collection names to search; empty = every ingested collection. */
  collections: string[];
  /** Provider ids to search; empty = every provider. */
  providers: string[];
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
    providers: [],
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
    if (row.op === "in") {
      const items = splitList(row.value);
      if (!items.length) return { ok: false, error: `"${key}": give at least one value` };
      where.push({ key, in: items.map((v) => typedValue(row, v)) });
    } else if (row.op === "prefix") {
      const p = row.value.trim();
      if (!p) return { ok: false, error: `"${key}": give the start of the value` };
      where.push({ key, prefix: p });
    } else {
      const v = row.value.trim();
      if (!v) return { ok: false, error: `"${key}": give a value` };
      where.push({ key, eq: typedValue(row, v) });
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
  return {
    ok: true,
    query: {
      providers: form.providers.length ? [...form.providers] : null,
      collections: form.collections.length ? [...form.collections] : null,
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

/** A hit's identity across pages and partitions. */
export function hitKey(hit: Pick<SearchHit, "provider" | "collection" | "node_id">): string {
  return `${hit.provider}\u0000${hit.collection}\u0000${hit.node_id}`;
}

/** Every page fetched so far for one query, folded. */
export interface SearchResults {
  hits: SearchHit[];
  nextCursor: string | null;
  /** Totals per partition for the whole query, as the latest page reported them. */
  counts: Record<string, number>;
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
    tookMs: page.took_ms ?? 0,
    totalTookMs: (prev?.totalTookMs ?? 0) + (page.took_ms ?? 0),
    pages: (prev?.pages ?? 0) + 1,
  };
}

export interface PartitionCount {
  provider: string;
  collection: string;
  count: number;
}

/** `counts` as rows, largest first. The key is `<provider>/<collection>`; a provider id carries no
 *  `/`, so the first one splits it (a collection name may carry one). */
export function partitionCounts(counts: Record<string, number>): PartitionCount[] {
  return Object.entries(counts)
    .map(([k, count]) => {
      const i = k.indexOf("/");
      return i < 0 ? { provider: "", collection: k, count } : { provider: k.slice(0, i), collection: k.slice(i + 1), count };
    })
    .sort((a, b) => b.count - a.count || a.collection.localeCompare(b.collection));
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
        const op: FilterOp = x.op === "in" || x.op === "prefix" ? x.op : "eq";
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
  return {
    collections: strs(o.collections),
    providers: strs(o.providers),
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
