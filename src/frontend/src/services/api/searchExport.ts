// Export of attribute-search hits: one model per hit, written into a folder of a scope the user can
// write to (`ada/comms/rest/routes/attribute_search.py`, `/assets/search/export*`), and the
// provenance every written file carries (`/files/meta`).
//
//   preflight POST the same query/hits -> per collection: hits with geometry ready, missing (a
//                  request can fix), unknown to the geometry provider's tree; writes nothing
//   start    POST  the query (and the picked hits, or null for every match) -> a run, 202
//   runs     GET   this scope's export runs, newest first, each with its items counted by status
//   run      GET   one run with its items: per (provider, collection) one job, its timings and
//                  what each file came to (written / no geometry / failed)
//   cancel   POST  stop the whole run
//   skip     POST  give up on ONE running item and carry on with the rest -- the position is
//                  required, because up to four items run at once and "the current one" is not
//                  one item
//   meta     GET   a file's provenance: where it came from (provider, collection, node, revision),
//                  the query that found it, what it matched; 404 when it has none
//
// The run lives in the SEARCHED scope (that is where the index and the query are); the files land
// in the TARGET scope. Every call below that reads a run takes the searched scope; the gallery and
// the sidecars read the target scope.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail, type ScopeUrl } from "./client";
import type { SweepRun } from "./assetSchedules";
import type { SearchPathEntry, SearchQuery } from "./attributeSearch";

/** A hit as the export takes it -- what the search returned, minus nothing it needs. */
export interface ExportHit {
  provider: string;
  collection: string;
  node_id: string;
  full_name: string;
  source_ref: string | null;
  kind: string | null;
  depth: number | null;
  path: SearchPathEntry[];
  attrs: Record<string, string | number | boolean>;
}

export type ExportAlso = "glb" | "step";

/** What a file is made of: `design` -- the design system's own geometry as IFC (exact; requested
 *  from it first when missing, which is slow); `mesh` -- the hit's triangles as its own provider
 *  published them, as a GLB (quick, for looking at). A mesh file can ask for its design geometry
 *  afterwards (`requestDesignGeometry`). */
export type ExportGeometry = "design" | "mesh";

export interface ExportRequest {
  /** Default `design`. A `mesh` export takes neither `also` nor `request_missing_geometry`. */
  geometry?: ExportGeometry;
  /** The search body exactly as sent to `/assets/search`; cursor and limit are ignored. */
  query: SearchQuery;
  /** The hits to export; null = every match of `query` (the server pages through it). */
  hits: ExportHit[] | null;
  target_scope: ScopeUrl;
  /** Relative, no leading slash. */
  target_folder: string;
  include_source_attributes: boolean;
  also: ExportAlso[];
  /** Ask the geometry provider for the hits it has no published geometry for, and publish its
   *  answers, before writing the files -- the user's answer to the preflight's prompt. Default
   *  false. */
  request_missing_geometry?: boolean;
  /** One file per collection holding all of its hits, instead of one per hit. Default false. */
  merge?: boolean;
  /** Mesh exports: a collection whose provider published no project tree asks for it first, inside
   *  the run ("request trees, then export"). Default false. */
  request_trees?: boolean;
}

/** One collection of a preflight: how its hits stand against the geometry provider's published
 *  content. `missing` = `listed` (in its tree, no geometry) + `unpublished` (it published nothing in
 *  the collection) -- what a request can fix; `unknown` = not in its tree at all. */
export interface PreflightCollection {
  collection: string;
  hits: number;
  ready: number;
  missing: number;
  listed: number;
  unpublished: number;
  unknown: number;
  /** Request jobs the missing hits would take. */
  jobs: number;
  missing_reason: string | null;
  unknown_reason: string | null;
  unknown_examples: string[];
}

/** `POST /assets/search/export/preflight`: what an export would find, before anything is written. */
export interface ExportPreflight {
  geometry_provider: string;
  hits: number;
  ready: number;
  missing: number;
  unknown: number;
  jobs: number;
  collections: PreflightCollection[];
  request: {
    /** An online worker of the geometry provider declares a node request. */
    available: boolean;
    /** ...and this user may run it. */
    allowed: boolean;
    reason: string | null;
    plugin_id: string | null;
    label: string | null;
    per_job: number;
  };
  /** A sweep of the geometry provider is running: requests share its seat. */
  sweep_running: { id: string; created_at: string | null } | null;
  took_ms: number;
}

/** One (provider, collection) of a mesh preflight: its hits against the provider's published tree. */
export interface MeshPreflightCollection {
  provider: string;
  collection: string;
  hits: number;
  ready: number;
  /** Hits of a collection the provider has published no project tree for. */
  no_tree: number;
  /** Hits its tree does not hold (or holds without geometry). */
  unknown: number;
  unknown_reason: string | null;
  unknown_examples: string[];
}

/** A provider's collection request, as the mesh preflight offers it for its collections with no tree. */
export interface TreeRequestOffer {
  provider: string;
  collections: string[];
  available: boolean;
  allowed: boolean;
  reason: string | null;
  label: string | null;
  plugin_id: string | null;
  sweep_running: { id: string; created_at: string | null } | null;
}

/** `POST /assets/search/export/preflight` with `geometry: "mesh"`. */
export interface MeshPreflight {
  geometry: "mesh";
  hits: number;
  ready: number;
  no_tree: number;
  unknown: number;
  collections: MeshPreflightCollection[];
  tree_requests: TreeRequestOffer[];
  took_ms: number;
}

/** One request job of an item's geometry request. */
export interface ExportRequestBatch {
  nodes: number;
  status?: "published" | "unchanged" | "nothing" | "error";
  message?: string | null;
  job_id?: string;
  publish_job_id?: string;
  revision?: string | null;
  /** How the provider's job got its session: e.g. `adopted` (reused the open project) or a fresh login. */
  login_mode?: string | null;
  login_ms?: number;
  export_ms?: number;
  stage_ms?: number;
  queued_ms?: number;
  fetch_ms?: number;
  publish_ms?: number;
}

export interface ExportFileCounts {
  written: number;
  no_geometry: number;
  failed: number;
}

export interface ExportJobTimings {
  resolve_ms?: number;
  read_ms?: number;
  ifc_ms?: number;
  attrs_ms?: number;
  convert_ms?: number;
  thumb_ms?: number;
  upload_ms?: number;
}

export interface ExportItemTimings {
  /** A mesh export's build phase: resolving the hits against their provider's publish, the builds (wall-clock), and
   *  how many of them were already built. */
  build_resolve_ms?: number;
  build_ms?: number;
  builds?: number;
  builds_cached?: number;
  queued_ms?: number;
  run_ms?: number;
  total_ms?: number;
  job?: ExportJobTimings;
  /** The geometry request, when the export asked for one: finding what lacks geometry, the
   *  request jobs (queued + run), publishing their answers, and one row per job. */
  request_resolve_ms?: number;
  request_ms?: number;
  request_publish_ms?: number;
  request_batches?: ExportRequestBatch[];
  request?: { missing: number; unknown: number; jobs: number; published: number; logins: Record<string, number> };
}

export interface ExportFileError {
  node_id: string;
  full_name: string;
  status: "no_geometry" | "error";
  error: string | null;
}

/** `requesting`: asking the geometry provider for missing geometry, before the export job. */
export type ExportItemStatus = "pending" | "requesting" | "running" | "done" | "partial" | "error" | "skipped";

/** One (provider, collection) of a run: one job writing every hit of it. */
export interface ExportItem {
  position: number;
  provider: string;
  collection: string;
  hits: number;
  status: ExportItemStatus;
  job_id: string | null;
  message: string | null;
  error: string | null;
  timings: ExportItemTimings;
  files: ExportFileCounts;
  file_errors: ExportFileError[];
  started_at: string | null;
  finished_at: string | null;
  /** Live, while running only. */
  stage?: string | null;
  progress?: number | null;
}

export interface ExportRun {
  id: string;
  /** The searched scope. */
  scope: ScopeUrl;
  target_scope: ScopeUrl;
  target_folder: string;
  status: "running" | "done" | "error" | "cancelled" | "interrupted";
  /** Items (partitions), not hits. */
  total: number;
  hits: number;
  query: SearchQuery | null;
  options: {
    include_source_attributes: boolean;
    also: ExportAlso[];
    request_missing_geometry?: boolean;
    geometry?: ExportGeometry;
    merge?: boolean;
  };
  requested_by: string | null;
  created_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  error: string | null;
  /** A skip asked for and not yet carried out. */
  skip_position: number | null;
  files: ExportFileCounts;
  /** On the listing only: items by status. */
  counts?: Partial<Record<ExportItemStatus, number>>;
  /** On a single run's read only. */
  items?: ExportItem[];
}

/** `ada.files/meta@1` -- a written file's provenance. Any field may be null when it was read back
 *  from the file itself (IFC / GLB provenance) rather than from its sidecar. */
export interface FileMeta {
  schema: string;
  file: string | null;
  format: string | null;
  created_at: string | null;
  created_by: string | null;
  source: {
    provider: string | null;
    scope: ScopeUrl | null;
    collection: string | null;
    node_id: string | null;
    full_name: string | null;
    source_ref: string | null;
    revision: string | null;
    path: SearchPathEntry[] | null;
  } | null;
  query: SearchQuery | null;
  matched: Record<string, unknown> | null;
  run_id: string | null;
  attributes: { elements: number; with_attrs: number; without_attrs: number } | null;
  /** `mesh`: the quick-look GLB a design file was requested from (only then). */
  derived: { glb: string | null; step: string | null; thumb: string | null; mesh?: string | null } | null;
  /** Where the geometry came from. `kind` is absent on files written before mesh exports (= design). */
  geometry?: {
    kind?: ExportGeometry;
    provider: string | null;
    subject: string | null;
    revision: string | null;
    node: string | null;
    /** The file holds a whole collection's hits (a merged export); `nodes` are theirs. */
    merged?: boolean;
    nodes?: string[];
  } | null;
  /** Every element of the file that carries an `ADA_Source` set, by IFC name: the levels root
   *  first, the hit, then the members matched to an index row. Absent on files written before it
   *  was recorded, and on GLB provenance. */
  elements?: FileMetaElement[] | null;
  /** A merged file: the hits inside it, in file order. */
  joints?: ExportJoint[] | null;
  origin: "sidecar" | "ifc" | "glb" | null;
}

/** One element's `ADA_Source` set, as the export wrote it into the IFC. */
export interface SourceSet {
  provider: string | null;
  /** The scope the search ran in (path form), so one element is enough to find its node. */
  scope?: ScopeUrl | null;
  collection: string | null;
  node_id: string | null;
  full_name: string | null;
  source_ref?: string | null;
}

export interface FileMetaElement {
  /** The IFC product's Name -- what the viewer's tree labels it. */
  name: string;
  /** The IFC product's GlobalId. */
  guid: string | null;
  role: "level" | "hit" | "part" | "member" | null;
  source: SourceSet;
}

/** `<folder>/_export.json` (`ada.files/export@1`): the files in export order. */
export interface ExportDoc {
  schema: string;
  run_id: string;
  query: SearchQuery | null;
  created_at: string | null;
  folder: string;
  files: {
    file: string;
    collection: string;
    provider: string;
    node_id: string;
    full_name: string;
    meta: string;
    thumb: string | null;
    /** A merged file only: how many hits it holds, and those hits in file order. */
    hits?: number;
    joints?: ExportJoint[];
  }[];
}

/** One hit inside a merged export file. */
export interface ExportJoint {
  node_id: string | null;
  full_name: string | null;
  /** Its node's name in the file's tree. */
  name: string;
  /** Its ancestors in the source tree, root first. Absent on exports written before it was kept. */
  path?: SearchPathEntry[];
  /** `[first, last]` draw-range ids (inclusive) that are its objects, when the file says so (a
   *  merged mesh GLB); otherwise it is found by `name`. */
  ids?: [number, number] | null;
  /** Its own thumbnail's key, when one was rendered. */
  thumb: string | null;
}

function exportBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets/search/export`;
}

function blobUrl(scope: ScopeUrl, key: string): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
}

function post(body?: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) };
}

export const searchExportApi = {
  /** What an export of `body` would find against the geometry provider. Writes nothing. */
  async exportPreflight(scope: ScopeUrl, body: Pick<ExportRequest, "query" | "hits">): Promise<ExportPreflight> {
    const r = await authedFetch(`${exportBase(scope)}/preflight`, post(body));
    return jsonOrThrow(r, "exportPreflight");
  },

  /** The preflight of a MESH export: each hit against its own provider's published project tree. */
  async meshExportPreflight(scope: ScopeUrl, body: Pick<ExportRequest, "query" | "hits">): Promise<MeshPreflight> {
    const r = await authedFetch(`${exportBase(scope)}/preflight`, post({ ...body, geometry: "mesh" }));
    return jsonOrThrow(r, "meshExportPreflight");
  },

  /** Ask `provider` for the project trees of `collections` (its collection request, once each), as one
   *  run driven like a sweep. 403 when its job is admin-only, 409 while a sweep of it runs. */
  async requestTrees(scope: ScopeUrl, provider: string, collections: string[]): Promise<SweepRun> {
    const r = await authedFetch(`${exportBase(scope)}/request-trees`, post({ provider, collections }));
    return jsonOrThrow(r, "requestTrees");
  },

  /** A tree request started from `scope`, with its items' live progress. */
  async treeRequestRun(scope: ScopeUrl, runId: string): Promise<SweepRun> {
    const r = await authedFetch(`${exportBase(scope)}/request-trees/${encodeURIComponent(runId)}`);
    return jsonOrThrow(r, "treeRequestRun");
  },

  /** Start an export of `body.hits` (or every match) from `scope`. 409 over the hit cap. */
  async startSearchExport(scope: ScopeUrl, body: ExportRequest): Promise<ExportRun> {
    const r = await authedFetch(exportBase(scope), post(body));
    return jsonOrThrow(r, "startSearchExport");
  },

  async searchExportRuns(scope: ScopeUrl, limit = 20): Promise<ExportRun[]> {
    const r = await authedFetch(`${exportBase(scope)}/runs?limit=${limit}`);
    return (await jsonOrThrow<{ runs: ExportRun[] }>(r, "searchExportRuns")).runs;
  },

  async searchExportRun(scope: ScopeUrl, runId: string): Promise<ExportRun> {
    const r = await authedFetch(`${exportBase(scope)}/runs/${encodeURIComponent(runId)}`);
    return jsonOrThrow(r, "searchExportRun");
  },

  async cancelSearchExport(scope: ScopeUrl, runId: string): Promise<{ cancelled: string }> {
    const r = await authedFetch(`${exportBase(scope)}/runs/${encodeURIComponent(runId)}/cancel`, post());
    return jsonOrThrow(r, "cancelSearchExport");
  },

  /** Drive the run again over what it did not finish -- and, with `retryFailed`, what it did not
   *  write -- into the same run and folder. Idempotent: a running run, or one with nothing left,
   *  answers `rerun: false` with why. */
  async rerunSearchExport(
    scope: ScopeUrl,
    runId: string,
    retryFailed = true,
  ): Promise<ExportRun & { rerun: boolean; reason: string | null }> {
    const r = await authedFetch(
      `${exportBase(scope)}/runs/${encodeURIComponent(runId)}/rerun`,
      post({ retry_failed: retryFailed }),
    );
    return jsonOrThrow(r, "rerunSearchExport");
  },

  /** Skip the running item at `position` and carry on. 409 when that item is not running. */
  async skipSearchExportItem(scope: ScopeUrl, runId: string, position: number): Promise<{ skip_requested: number }> {
    const r = await authedFetch(
      `${exportBase(scope)}/runs/${encodeURIComponent(runId)}/skip?position=${position}`,
      post(),
    );
    return jsonOrThrow(r, "skipSearchExportItem");
  },

  /** Ask for the design system's own geometry of the node a MESH file came from: a one-hit design
   *  export (requesting the geometry first when it is missing and the user may) that writes
   *  `<stem>.ifc` beside the GLB. Answers the run, which lives in the file's SOURCE scope
   *  (`run.scope`); `requesting` says whether a request will be made. */
  async requestDesignGeometry(scope: ScopeUrl, key: string): Promise<ExportRun & { requesting: boolean }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files/request-geometry`,
      post({ key }),
    );
    return jsonOrThrow(r, "requestDesignGeometry");
  },

  /** A file's provenance; null when it has none (404). */
  async fileMeta(scope: ScopeUrl, key: string, signal?: AbortSignal): Promise<FileMeta | null> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/files/meta?key=${encodeURIComponent(key)}`,
      { signal },
    );
    if (r.status === 404) return null;
    return jsonOrThrow(r, "fileMeta");
  },

  /** A folder's `_export.json`, read by key (the listing hides it); null when there is none. */
  async exportDoc(scope: ScopeUrl, folder: string, signal?: AbortSignal): Promise<ExportDoc | null> {
    const key = `${folder.replace(/^\/+|\/+$/g, "")}/_export.json`;
    const r = await authedFetch(blobUrl(scope, key), { signal });
    if (r.status === 404) return null;
    if (!r.ok) throw new ApiError(`exportDoc(${key})`, r.status, await readDetail(r));
    try {
      const doc = (await r.json()) as ExportDoc;
      return doc && Array.isArray(doc.files) ? doc : null;
    } catch {
      return null; // half-written or not ours: order alphabetically instead
    }
  },

  /** A sidecar image (`.thumb.png`) as an object URL -- fetched with auth, so an `<img src>` to
   *  the blob route would not do. Null when it is not there. The caller revokes it. */
  async blobObjectUrl(scope: ScopeUrl, key: string, signal?: AbortSignal): Promise<string | null> {
    const r = await authedFetch(blobUrl(scope, key), { signal });
    if (!r.ok) return null;
    return URL.createObjectURL(await r.blob());
  },
};
