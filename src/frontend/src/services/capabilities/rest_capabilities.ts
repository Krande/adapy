// REST implementations of the viewer capabilities (hosted viewer mode).
//
// These are thin adapters over `services/viewerApi` — the HTTP client stays the
// implementation detail and the seam in `types.ts` is what domain code sees.
// Behaviour here is deliberately identical to what `statsStore` used to call
// directly, so the hosted viewer is unchanged by the introduction of the seam.
//
// `viewerApi` is pulled in with a dynamic import rather than a static one: it
// transitively initialises browser-only state (the OIDC client reads
// sessionStorage at module scope), and the websocket build has no business
// evaluating the REST HTTP client just because the capability index names both
// implementations. The module is cached after the first call.

import type { ModelStats } from "@/utils/stats/modelStats";
import type {
  ConvertResponse,
  ProceduralBlueprintOption,
  ProceduralCompileResponse,
  ProceduralDoc,
  ProceduralEngineResolved,
  ProceduralModelDetail,
  ProceduralRelocationResult,
  ProceduralXlsxDetect,
} from "@/services/viewerApi";
import {
  CapabilityUnavailableError,
  ProceduralCommitConflictError,
  type CapabilityTransport,
  type ModelStatsCapability,
  type ModelStatsResult,
  type ModelStatsSource,
  type ProceduralBuildOptions,
  type ProceduralCatalogKind,
  type ProceduralCatalogSyncResult,
  type ProceduralCatalogs,
  type ProceduralCompileLog,
  type ProceduralCommitResult,
  type ProceduralExportFormat,
  type ProceduralExportOptions,
  type ProceduralModelCapability,
  type ProceduralModelEntry,
  type ProceduralModelResult,
  type ProceduralModelSource,
  type ProceduralRelocationResponse,
  type ProceduralResyncResult,
  type ProceduralSyncableCatalogKind,
  type ProceduralVerb,
  type ProceduralXlsxImportRequest,
  type ProceduralXlsxImportResponse,
  type StatsExportFormat,
  type ViewerCapabilities,
} from "./types";

type ViewerApiModule = typeof import("@/services/viewerApi");

/** The cached dynamic import shared by the REST capabilities (see the module comment). */
async function api(): Promise<ViewerApiModule> {
  return await import("@/services/viewerApi");
}

export class RESTModelStatsCapability implements ModelStatsCapability {
  readonly transport: CapabilityTransport = "rest";

  async fetchStats(source: ModelStatsSource): Promise<ModelStatsResult> {
    const { scope, modelId, derivedKey } = source;
    if (!scope || !modelId || !derivedKey) return { available: false };
    const { viewerApi } = await api();
    return await viewerApi.fetchModelStats(scope, modelId, derivedKey);
  }

  /** The server-side sidecar is the authority in REST mode — see the interface
   * docstring. Always false. */
  adoptEmbeddedStats(_stats: ModelStats | null): boolean {
    return false;
  }

  canExport(source: ModelStatsSource): boolean {
    return Boolean(source.scope && source.modelId && source.derivedKey);
  }

  async exportStats(source: ModelStatsSource, fmt: StatsExportFormat, tab?: string): Promise<void> {
    const { scope, modelId, derivedKey } = source;
    if (!scope || !modelId || !derivedKey) return;
    const { viewerApi } = await api();
    await viewerApi.downloadStatsExport(scope, modelId, derivedKey, fmt, tab);
  }
}


export class RESTProceduralModelCapability implements ProceduralModelCapability {
  readonly transport: CapabilityTransport = "rest";

  /** The stored model is editable and committable, so the panels stay interactive. */
  readonly canEdit = true;

  /** Every verb on this interface is implemented over REST -- this capability predates
   * `supports` and none of its behaviour is conditional on it -- except `listModels`: that verb
   * addresses a local-disk directory the hosted viewer has no equivalent of (a per-scope model
   * listing is a different, already-existing REST endpoint the panels reach some other way), so it
   * is the one verb REST reports as NOT supported rather than growing a REST implementation with
   * nothing to list. */
  supports(verb: ProceduralVerb): boolean {
    return verb !== "listModels";
  }

  /** Unreachable behind `supports("listModels") === false` -- see that method's docstring. Throws
   * rather than resolving to `[]` so a caller that skips the `supports` gate fails loudly instead
   * of rendering a local-models browser that is permanently, silently empty. */
  async listModels(_scope: string): Promise<ProceduralModelEntry[]> {
    throw new CapabilityUnavailableError("listModels", this.transport);
  }

  async fetchModel(source: ProceduralModelSource): Promise<ProceduralModelResult> {
    const { scope, modelId } = source;
    if (!scope || !modelId) return { available: false };
    try {
      const { viewerApi } = await api();
      const detail = await viewerApi.getProceduralModel(scope, modelId);
      return { available: Boolean(detail?.doc), doc: detail?.doc ?? null };
    } catch {
      // A model that cannot be fetched is reported absent rather than thrown: the panels degrade
      // to empty, exactly as they do for an assembly that never had a procedural document.
      return { available: false };
    }
  }

  /** The stored model is the authority here; an embedded copy must not race it. */
  adoptEmbeddedModel(_doc: ProceduralDoc | null): boolean {
    return false;
  }

  // ---- Catalogs -----------------------------------------------------------------------------

  async listCatalog<K extends ProceduralCatalogKind>(scope: string, kind: K): Promise<ProceduralCatalogs[K]> {
    const { viewerApi } = await api();
    // One endpoint per catalog on the REST side; the seam keys them by kind. Typed through the
    // `ProceduralCatalogs` map so a consumer asking for `"engines"` gets `ProceduralEngineSummary[]`.
    const byKind: { [P in ProceduralCatalogKind]: (scope: string) => Promise<ProceduralCatalogs[P]> } = {
      equipmentTypes: (s) => viewerApi.proceduralEquipmentTypes(s),
      cellTypes: (s) => viewerApi.proceduralCellTypes(s),
      openingTypes: (s) => viewerApi.proceduralOpeningTypes(s),
      systemTypes: (s) => viewerApi.proceduralSystemTypes(s),
      designRulesets: (s) => viewerApi.proceduralDesignRulesets(s),
      engines: (s) => viewerApi.listProceduralEngines(s),
      detailingEngines: (s) => viewerApi.listDetailingEngines(s),
    };
    return await byKind[kind](scope);
  }

  async listBlueprints(scope: string, engine: string): Promise<ProceduralBlueprintOption[]> {
    const { viewerApi } = await api();
    return await viewerApi.proceduralBlueprints(scope, engine);
  }

  async syncCatalogEntry(
    scope: string,
    kind: ProceduralSyncableCatalogKind,
    slug: string,
  ): Promise<ProceduralCatalogSyncResult> {
    const { viewerApi } = await api();
    return kind === "equipmentTypes"
      ? await viewerApi.syncProceduralEquipmentType(scope, slug)
      : await viewerApi.syncProceduralSystemType(scope, slug);
  }

  async resyncEquipmentTypes(scope: string): Promise<ProceduralResyncResult> {
    const { viewerApi } = await api();
    return await viewerApi.resyncProceduralEquipmentTypes(scope);
  }

  // ---- Model revisions ----------------------------------------------------------------------

  async commitModel(
    scope: string,
    modelId: string,
    doc: ProceduralDoc,
    baseRevision: number,
  ): Promise<ProceduralCommitResult> {
    const { viewerApi, ApiError } = await api();
    try {
      return await viewerApi.commitProceduralModel(scope, modelId, doc, baseRevision);
    } catch (e) {
      // The REST client reports a stale base revision as HTTP 409; the seam names it.
      if (e instanceof ApiError && e.status === 409) {
        throw new ProceduralCommitConflictError(modelId, baseRevision);
      }
      throw e;
    }
  }

  // ---- Build jobs ---------------------------------------------------------------------------

  async compileModel(
    scope: string,
    modelId: string,
    opts: ProceduralBuildOptions,
  ): Promise<ProceduralCompileResponse> {
    const { viewerApi } = await api();
    return await viewerApi.compileProceduralModel(
      scope,
      modelId,
      opts.force ?? false,
      opts.lod ?? "sim",
      opts.engine,
      opts.detailing,
      opts.detailingOptions,
    );
  }

  async previewModel(
    scope: string,
    modelId: string,
    doc: unknown,
    opts: ProceduralBuildOptions,
  ): Promise<ProceduralCompileResponse> {
    const { viewerApi } = await api();
    return await viewerApi.previewProceduralModel(scope, modelId, doc, opts);
  }

  async jobStatus(jobId: string): Promise<ConvertResponse> {
    const { viewerApi } = await api();
    return await viewerApi.convertStatus(jobId);
  }

  async fetchCompileLog(
    scope: string,
    modelId: string,
    derivedKey: string,
    runId?: string | null,
  ): Promise<ProceduralCompileLog> {
    const { viewerApi } = await api();
    return await viewerApi.proceduralCompileLog(scope, modelId, derivedKey, runId);
  }

  async resolveEngine(scope: string, engineId: string): Promise<ProceduralEngineResolved> {
    const { viewerApi } = await api();
    return await viewerApi.resolveProceduralEngine(scope, engineId);
  }

  // ---- Export -------------------------------------------------------------------------------

  async exportModel(
    scope: string,
    modelId: string,
    format: ProceduralExportFormat,
    opts?: ProceduralExportOptions,
  ): Promise<ProceduralCompileResponse> {
    const { viewerApi } = await api();
    // The workbook has its own endpoint (it is engine-specific); the CAD/analysis formats share
    // one. Both answer with the same job contract, which is why the seam has a single verb.
    if (format === "xlsx") {
      return await viewerApi.exportProceduralModelXlsx(scope, modelId, {
        engine: opts?.engine,
        force: opts?.force,
      });
    }
    return await viewerApi.exportProceduralModel(
      scope,
      modelId,
      format,
      opts ? { force: opts.force, cad: opts.cad } : undefined,
    );
  }

  async downloadArtifact(scope: string, key: string, suggestedName: string): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.downloadBlob(scope, key, suggestedName);
  }

  // ---- Import -------------------------------------------------------------------------------

  async stageXlsxImport(scope: string, data: Blob | ArrayBuffer): Promise<ProceduralXlsxDetect> {
    const { viewerApi } = await api();
    return await viewerApi.uploadProceduralImportXlsx(scope, data);
  }

  async importXlsx(scope: string, body: ProceduralXlsxImportRequest): Promise<ProceduralXlsxImportResponse> {
    const { viewerApi } = await api();
    return await viewerApi.importProceduralModelXlsx(scope, body);
  }

  async fetchImportedModel(scope: string, derivedKey: string): Promise<ProceduralModelDetail> {
    const { viewerApi } = await api();
    // The import job's result artifact is a small JSON naming the model it created.
    const result = await viewerApi.fetchProceduralImportResult(scope, derivedKey);
    return await viewerApi.getProceduralModel(scope, result.model_id);
  }

  // ---- Relocations --------------------------------------------------------------------------

  async proposeRelocations(scope: string, modelId: string): Promise<ProceduralRelocationResponse> {
    const { viewerApi } = await api();
    return await viewerApi.proposeProceduralRelocations(scope, modelId);
  }

  async fetchRelocations(scope: string, key: string): Promise<ProceduralRelocationResult> {
    const { viewerApi } = await api();
    return await viewerApi.fetchProceduralRelocations(scope, key);
  }

  // ---- Equipment preview --------------------------------------------------------------------

  async fetchEquipmentPreviewGlb(scope: string, typeId: string): Promise<ArrayBuffer | null> {
    const { viewerApi } = await api();
    const detail = await viewerApi.getEquipmentType(scope, typeId);
    if (!detail.preview_glb_key) return null;
    return await viewerApi.getBlob(scope, detail.preview_glb_key);
  }
}

export class RESTCapabilities implements ViewerCapabilities {
  readonly transport: CapabilityTransport = "rest";
  readonly stats: ModelStatsCapability = new RESTModelStatsCapability();
  readonly procedural: ProceduralModelCapability = new RESTProceduralModelCapability();
}
