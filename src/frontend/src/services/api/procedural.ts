// Procedural models: CRUD + compile/preview + stats/export for a scope's
// procedural documents. Catalog lookups (equipment/system/cell/opening
// types, engines, detailing engines) live in ./proceduralCatalog.ts.

import { runtime } from "@/runtime/config";

import type { ModelStats } from "@/utils/stats/modelStats";
import {
  ApiError,
  authedFetch,
  jsonOrThrow,
  readDetail,
  toQueryString,
  type ScopeUrl,
} from "./client";
import { filesApi } from "./files";
import type { DetailingOptionsPayload } from "./proceduralCatalog";

// ── Procedural cell models (cellbuilder) ─────────────────────────────

export interface ProceduralModelSummary {
  id: string;
  name: string;
  revision: number;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  latest_glb_key?: string | null;
}

export interface ProceduralModelDetail extends ProceduralModelSummary {
  doc: ProceduralDoc;
}

/** A start-from template for the "New model from template" menu. The list is
 * the union of the demo templates advertised by every currently-live worker
 * (base worker → adapy-default; capability workers → their own), so a template
 * shows exactly while a worker that can build it is up. Instantiation commits
 * `doc` verbatim (for a non-default engine, a thin routing document the engine
 * expands at compile time). */
export interface ProceduralTemplate {
  /** Stable identity (the template slug). */
  id: string;
  name: string;
  /** Engine slug shown in parentheses in the menu, e.g. an external engine. */
  engine: string;
  /** The document committed verbatim when the user picks this template. */
  doc: ProceduralDoc;
}

/** Entity dumps follow ada.topology.entities (TopoSpace / TopoEquipment). */
export interface ProceduralDoc {
  grid?: Record<string, unknown>;
  /** Blueprint compile options (whitelisted server-side), e.g.
   * {reinforce_internal_walls: true}. */
  blueprint?: Record<string, unknown>;
  /** Selected structural blueprint name the compiler dispatches on (the
   * engine-advertised blueprint slug, e.g. "steel_stru"/"none"). Kept OUT of the
   * whitelisted `blueprint` options. Absent = "steel_stru" (backward compatible). */
  blueprint_name?: string;
  /** Named design ruleset slug (routing/penetration rules) resolved by the
   * compiler; unknown/absent falls back to "standard". */
  design_rules?: string;
  /** Selected fabrication-detail engine slug (adds connection joints after the
   * structural build); absent/"none" = structural-only. The cellbuilder seeds
   * `selectedDetailing` from this on open and persists it back on commit. */
  detailing?: string;
  /** When true, catalog equipment with a linked CAD asset render as real CAD
   * geometry (spliced at compile) instead of a box. */
  equipment_cad?: boolean;
  /** Procedural engine slug that authored/compiles this doc; mirrored to the
   * model's engine column on commit so a cloned template routes to the right
   * worker. Absent / "adapy-default" = the built-in engine. */
  engine?: string;
  spaces: Record<string, unknown>[];
  equipments: Record<string, unknown>[];
  openings?: Record<string, unknown>[];
  /** Routed service runs; each entry {NAME, TYPE, MEDIUM?, CONNECTIONS:
   * [{EQUIPMENT, PORT}]}. Rendered by the compiler as pipe/cable runs. */
  systems?: Record<string, unknown>[];
  /** Authored loft (swept) members; each {NAME, STRUCTURE_NAME?, INCLUDE,
   * STATIONS:[{TYPE, X, Y, Z, WIDTH?/HEIGHT?/RADIUS?, SEGMENTS}], PLACEMENT?
   * (4x4 row-major), THICKNESS, SURFACE_ONLY}. A member with N stations
   * compiles to N-1 swept-band plates; the viewer draws band proxies. */
  loft_members?: Record<string, unknown>[];
  /** Cell GROUPS, each carrying its own structural blueprint (a group == one
   * structure). Each space's `STRUCTURE_NAME` names the group it belongs to;
   * ungrouped spaces omit it. Absent/empty = single model-level blueprint
   * (backward compatible). Honoured only by engines advertising
   * `supports_grouping`; the built-in engine ignores it. */
  groups?: { name: string; blueprint: string }[];
}

export interface ProceduralCompileResponse {
  job_id: string | null;
  derived_key: string;
  cached: boolean;
}

/** Result of staging an uploaded workbook for import. `engine` is the slug read
 * from the file's `_ADA_META` sheet, or null for a hand-made / legacy workbook
 * (no metadata) — the frontend then prompts the user to choose an engine. */
export interface ProceduralXlsxDetect {
  source_key: string;
  engine: string | null;
  package?: string | null;
  package_version?: string | null;
  schema_version?: string | null;
}

/** One proposed equipment move that would make a cramped/unroutable run clean.
 * ``from``/``to`` are equipment ORIGINS (X+LX/2, Y+LY/2, Z). */
export interface ProceduralRelocation {
  equipment: string;
  from: [number, number, number];
  to: [number, number, number];
  reason: string;
  fixes: string[];
}

export interface ProceduralRelocationResult {
  proposals: ProceduralRelocation[];
  unresolved: string[];
  baseline_problems: number;
}

export const proceduralApi = {
  // ── Procedural cell models (cellbuilder) ─────────────────────────
  //
  // Backed by /api/scopes/{scope}/procedural-models*; compile jobs
  // flow through the same NATS queue, so status polling reuses
  // convertStatus.

  async listProceduralModels(
    scope: ScopeUrl,
  ): Promise<ProceduralModelSummary[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models`,
    );
    const body = await jsonOrThrow<{ models: ProceduralModelSummary[] }>(
      r,
      `listProceduralModels(${scope})`,
    );
    return body.models;
  },

  /** Server-advertised start-from templates: the scope's seeded example models,
   * with worker-backed engines gated on a live worker. The adapy-default
   * built-ins are added client-side; this list is appended to them. */
  async listProceduralTemplates(
    scope: ScopeUrl,
  ): Promise<ProceduralTemplate[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-templates`,
    );
    const body = await jsonOrThrow<{ templates: ProceduralTemplate[] }>(
      r,
      `listProceduralTemplates(${scope})`,
    );
    return body.templates;
  },

  async createProceduralModel(
    scope: ScopeUrl,
    name: string,
  ): Promise<ProceduralModelDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
    return jsonOrThrow<ProceduralModelDetail>(
      r,
      `createProceduralModel(${name})`,
    );
  },

  /** Rename a procedural model — which is also how it MOVES between folders.
   *
   * The name carries the folder path; a model is addressed by UUID everywhere,
   * so a "/" in it is a label, not a route. One operation rather than two that
   * could disagree about where a model lives. */
  async renameProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    name: string,
  ): Promise<ProceduralModelSummary> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/name`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
    return jsonOrThrow<ProceduralModelSummary>(r, `renameProceduralModel(${name})`);
  },

  async getProceduralModel(
    scope: ScopeUrl,
    modelId: string,
  ): Promise<ProceduralModelDetail> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}`,
    );
    return jsonOrThrow<ProceduralModelDetail>(
      r,
      `getProceduralModel(${modelId})`,
    );
  },

  /** Commit the doc under optimistic concurrency. 409 (ApiError.status)
   * means someone else committed first — refetch and re-apply. */
  async commitProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    doc: ProceduralDoc,
    baseRevision: number,
  ): Promise<{ id: string; revision: number }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc, base_revision: baseRevision }),
      },
    );
    return jsonOrThrow<{ id: string; revision: number }>(
      r,
      `commitProceduralModel(${modelId})`,
    );
  },

  async deleteProceduralModel(scope: ScopeUrl, modelId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `deleteProceduralModel(${modelId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  async compileProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    force = false,
    lod: "sim" | "detail" = "sim",
    engine?: string | null,
    detailing?: string | null,
    detailingOptions?: DetailingOptionsPayload | null,
  ): Promise<ProceduralCompileResponse> {
    // force=true recompiles even if the revision's GLB is cached — used when the
    // compiler engine changed but the document (the cache key) didn't.
    // lod=detail compiles the richer detail model into a separate cache key.
    // engine selects the procedural engine. Pass it whenever the caller made an
    // explicit choice — INCLUDING "adapy-default": otherwise the server falls
    // back to the model's stored engine, so picking adapy-default on a capability-engine
    // model would silently still compile with that engine. Omit only when the
    // caller passes null/undefined (e.g. auto-compile on instantiate, which
    // should honour the model's stored engine). adapy-default shares the bare
    // cache key with the no-engine case, so this is cache-safe.
    const params = new URLSearchParams();
    if (force) params.set("force", "true");
    if (lod === "detail") params.set("lod", "detail");
    if (engine) params.set("engine", engine);
    // Detailing is a compile-time choice; "none" (the default) adds no key
    // suffix server-side, so omit it to keep the bare (backward-compat) key.
    if (detailing && detailing !== "none") params.set("detailing", detailing);
    // Per-joint detailing options ride as a JSON query param; folded into the
    // server's cache key (a knob change is a distinct entry) and passed to
    // detail(). Only meaningful when a detailing engine is selected.
    if (detailing && detailing !== "none" && detailingOptions)
      params.set("detailing_options", JSON.stringify(detailingOptions));
    const qs = toQueryString(params);
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/compile${qs}`,
      { method: "POST" },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `compileProceduralModel(${modelId})`,
    );
  },

  /** Build the CURRENT (uncommitted) document as an ephemeral preview — no
   * commit, no revision bump. The server keys the GLB on the doc's content hash
   * (re-previewing an unchanged doc is free) and, on a later commit of the same
   * doc, promotes this blob to the revision. `force` re-builds past the cache;
   * `engine`/`lod` mirror compileProceduralModel. */
  async previewProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    doc: unknown,
    opts?: {
      engine?: string | null;
      lod?: "sim" | "detail";
      force?: boolean;
      detailing?: string | null;
      detailingOptions?: DetailingOptionsPayload | null;
    },
  ): Promise<ProceduralCompileResponse> {
    const params = new URLSearchParams();
    if (opts?.force) params.set("force", "true");
    if (opts?.lod === "detail") params.set("lod", "detail");
    if (opts?.engine) params.set("engine", opts.engine);
    if (opts?.detailing && opts.detailing !== "none")
      params.set("detailing", opts.detailing);
    if (opts?.detailing && opts.detailing !== "none" && opts.detailingOptions)
      params.set("detailing_options", JSON.stringify(opts.detailingOptions));
    const qs = toQueryString(params);
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/compile-preview${qs}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc }),
      },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `previewProceduralModel(${modelId})`,
    );
  },

  /** Fetch the log of ONE compile run. Pass `runId` — the `job_id` the
   * compile/preview response returned — and you get exactly that run's log, so a
   * recompile of an unchanged document can never be handed the previous run's
   * output. `derivedKey` is the fallback for a result served from cache (no run
   * happened just now): the server resolves the artifact's `.run` pointer to
   * whichever run last targeted it. Returns `{text, runId}` — `runId` is the run
   * the server actually served ("" for a pre-runs artifact), so the caller can
   * tell a fresh log from an inherited one. Never throws on a missing log. */
  async proceduralCompileLog(
    scope: ScopeUrl,
    modelId: string,
    derivedKey: string,
    runId?: string | null,
  ): Promise<{ text: string; runId: string }> {
    const qs = runId
      ? `?run=${encodeURIComponent(runId)}`
      : `?key=${encodeURIComponent(derivedKey)}`;
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/compile-log${qs}`,
    );
    if (!r.ok) return { text: "", runId: "" };
    return {
      text: await r.text(),
      runId: r.headers.get("X-Compile-Run") || runId || "",
    };
  },

  /** Fetch the quantity take-off computed alongside a compiled GLB (the data
   * behind the viewer Stats panel). `derivedKey` is the compile response's GLB
   * key; the stats are its `.stats.json` sibling. Returns `{available:false}`
   * (HTTP 200) for models without a take-off (a capability engine / STEP-IFC imports) so
   * the panel degrades gracefully rather than erroring. */
  async fetchModelStats(
    scope: ScopeUrl,
    modelId: string,
    derivedKey: string,
  ): Promise<{ available: boolean; stats?: ModelStats }> {
    const qs = `?key=${encodeURIComponent(derivedKey)}`;
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/stats${qs}`,
    );
    if (!r.ok) return { available: false };
    return (await r.json()) as { available: boolean; stats?: ModelStats };
  },

  /** Download the take-off export — a whole-model `.xlsx` workbook (`fmt:"xlsx"`,
   * one sheet per discipline + COGs + Overview) or the active tab as `.csv`
   * (`fmt:"csv"`, `tab` = the open discipline tab). Built on the fly from the
   * stored stats sidecar; fetched WITH auth (bearer rides on the request) and
   * saved via an object URL so it works in both auth-on and auth-off modes. */
  async downloadStatsExport(
    scope: ScopeUrl,
    modelId: string,
    derivedKey: string,
    fmt: "xlsx" | "csv",
    tab?: string,
  ): Promise<void> {
    const params = new URLSearchParams({ key: derivedKey, fmt });
    if (tab) params.set("tab", tab);
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/stats/export?${params.toString()}`,
    );
    if (!r.ok) {
      throw new ApiError(`downloadStatsExport(${fmt})`, r.status, await readDetail(r));
    }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    const m = /filename="?([^"]+)"?/.exec(cd);
    const suggestedName = m ? m[1] : `stats.${fmt}`;
    const url = URL.createObjectURL(blob);
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = suggestedName;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } finally {
      URL.revokeObjectURL(url);
    }
  },

  /** Enqueue an export of the model's committed revision to its engine's Excel
   * workbook. Poll `convertStatus(job_id)`; on `done` the `.xlsx` lives at
   * `derived_key` — download it as an attachment (blob GET). `cached:true` +
   * `job_id:null` means the workbook was already built. */
  async exportProceduralModelXlsx(
    scope: ScopeUrl,
    modelId: string,
    opts?: { engine?: string | null; force?: boolean },
  ): Promise<ProceduralCompileResponse> {
    const params = new URLSearchParams();
    if (opts?.force) params.set("force", "true");
    if (opts?.engine) params.set("engine", opts.engine);
    const qs = toQueryString(params);
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/export-xlsx${qs}`,
      { method: "POST" },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `exportProceduralModelXlsx(${modelId})`,
    );
  },

  /** Enqueue an export of the committed revision to a downloadable CAD/analysis
   * file: `format: "ifc"` (the DETAIL model — clash cuts as IfcRelVoidsElement
   * voids, equipment as IfcPump/IfcTank/…), `"gxml"` (the SIMULATION model as a
   * Genie concept XML) or `"gnx"` (that XML as a Genie workspace). Built-in engine
   * only. Poll `convertStatus` then `downloadBlob`, exactly like
   * `exportProceduralModelXlsx`. */
  async exportProceduralModel(
    scope: ScopeUrl,
    modelId: string,
    format: "ifc" | "gxml" | "gnx",
    opts?: { force?: boolean; cad?: boolean },
  ): Promise<ProceduralCompileResponse> {
    const params = new URLSearchParams({ format });
    if (opts?.force) params.set("force", "true");
    // IFC only: splice real catalog CAD geometry for equipment (default on server-
    // side). Only send when explicitly off, to keep the default URL clean.
    if (format === "ifc" && opts?.cad === false) params.set("cad", "false");
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/export-model?${params.toString()}`,
      { method: "POST" },
    );
    return jsonOrThrow<ProceduralCompileResponse>(
      r,
      `exportProceduralModel(${modelId}, ${format})`,
    );
  },

  /** Stage an uploaded `.xlsx` for import and auto-detect its owning engine from
   * the file's `_ADA_META` sheet (read server-side, dependency-free). Returns the
   * staged `source_key` + detected `engine` (null when the workbook has no
   * metadata — the caller then prompts). Pass the result to
   * `importProceduralModelXlsx`. */
  async uploadProceduralImportXlsx(
    scope: ScopeUrl,
    data: Blob | ArrayBuffer,
  ): Promise<ProceduralXlsxDetect> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/import-xlsx/upload`,
      {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: data,
      },
    );
    return jsonOrThrow<ProceduralXlsxDetect>(r, `uploadProceduralImportXlsx`);
  },

  /** Enqueue an import of a staged workbook into a NEW model, built by `engine`'s
   * capability pool. Poll `convertStatus(job_id)`; on `done` GET the JSON result
   * blob at `derived_key` (`{model_id, name, engine, revision}`) to open it. */
  async importProceduralModelXlsx(
    scope: ScopeUrl,
    body: { source_key: string; engine: string; name: string },
  ): Promise<{ job_id: string; derived_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/import-xlsx`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow<{ job_id: string; derived_key: string }>(
      r,
      `importProceduralModelXlsx(${body.name})`,
    );
  },

  /** Enqueue a relocation analysis: propose the minimum equipment moves that
   * would make the model's cramped/unroutable runs clean. Returns a job to poll
   * (convertStatus); on done, GET the derived_key blob via
   * fetchProceduralRelocations. Never applied automatically. */
  async proposeProceduralRelocations(
    scope: ScopeUrl,
    modelId: string,
  ): Promise<{ job_id: string | null; derived_key: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/procedural-models/${encodeURIComponent(modelId)}/propose-relocations`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `proposeProceduralRelocations(${modelId})`);
  },

  /** Fetch the relocation proposals JSON produced by the worker. */
  async fetchProceduralRelocations(
    scope: ScopeUrl,
    key: string,
  ): Promise<ProceduralRelocationResult> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    if (!r.ok) {
      throw new ApiError(
        `fetchProceduralRelocations(${key})`,
        r.status,
        await readDetail(r),
      );
    }
    return (await r.json()) as ProceduralRelocationResult;
  },

  /** Fetch an import job's JSON result blob (`{model_id, name, engine,
   * revision}`) written by the worker at `derived_key`. */
  async fetchProceduralImportResult(
    scope: ScopeUrl,
    key: string,
  ): Promise<{ model_id: string; name: string; engine: string | null; revision: number }> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    if (!r.ok) {
      throw new ApiError(
        `fetchProceduralImportResult(${key})`,
        r.status,
        await readDetail(r),
      );
    }
    return (await r.json()) as {
      model_id: string;
      name: string;
      engine: string | null;
      revision: number;
    };
  },
};
