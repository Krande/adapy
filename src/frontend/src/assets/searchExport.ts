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

import type { ExportDoc, ExportPreflight, ExportRequestBatch, FileMeta } from "@/services/api/searchExport";
import type { SearchHit, SearchQuery } from "@/services/api/attributeSearch";
import { collectFolderPaths, isUiHiddenKey } from "@/utils/storage/fileTree";

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
}

export function geometryPrompt(pre: ExportPreflight): GeometryPrompt {
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
  };
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
    .sort((a, b) => rank(a.kind) - rank(b.kind) || a.name.localeCompare(b.name))
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
  if (q.providers?.length) parts.push(`providers ${q.providers.join(", ")}`);
  if (q.kind?.length) parts.push(`kind ${q.kind.join(", ")}`);
  if (q.depth) {
    if ("eq" in q.depth) parts.push(`depth ${q.depth.eq}`);
    else parts.push(`depth ${q.depth.min ?? ""}..${q.depth.max ?? ""}`);
  }
  for (const w of q.where ?? []) {
    if ("eq" in w) parts.push(`${w.key} = ${String(w.eq)}`);
    else if ("in" in w) parts.push(`${w.key} in [${w.in.map(String).join(", ")}]`);
    else parts.push(`${w.key} starts ${w.prefix}`);
  }
  return parts.join(" · ") || "everything";
}
