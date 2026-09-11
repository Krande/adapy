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

import { runtime } from "@/runtime/config";
import type { ModelStats } from "@/utils/stats/modelStats";
import type {
  ComponentSpecsResponse,
  ConvertResponse,
  EquipmentTypeDetail,
  EquipmentTypeDoc,
  EquipmentTypeSummary,
  FeaManifest,
  ProceduralBlueprintOption,
  ProceduralCompileResponse,
  ProceduralDoc,
  ProceduralEngineDetail,
  ProceduralEngineDoc,
  ProceduralEngineResolved,
  ProceduralModelDetail,
  ProceduralRelocationResult,
  ProceduralXlsxDetect,
  ScopeUrl,
  SystemTemplateDetail,
  SystemTemplateDoc,
  SystemTemplateSummary,
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
  // ---- seams 3 ----
  type CatalogCapability,
  type CatalogEntryFields,
  type CatalogJobHandle,
  type CatalogRevision,
  type CatalogVerb,
  type ComponentsCapability,
  type ComponentsVerb,
  type ConversionCapability,
  type ConversionVerb,
  type FeaCapability,
  type FeaManifestOptions,
  type FeaVerb,
  type FilesCapability,
  type FilesVerb,
  type MetricsCapability,
  type MetricsVerb,
  type PresignedDownload,
  type PresignedUpload,
  type RenderProfileRecord,
  type UploadProgressHandler,
  type ViewLoadRecord,
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

// =============================================================================
// Store- and scene-layer capabilities (seams 3). Every verb here is a one-line
// delegation to the `viewerApi` method the consumer used to call directly, so
// the hosted viewer's behaviour is unchanged; `supports()` is true throughout.
// =============================================================================

export class RESTFilesCapability implements FilesCapability {
  readonly transport: CapabilityTransport = "rest";

  supports(_verb: FilesVerb): boolean {
    return true;
  }

  async fetchBlob(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
    const { viewerApi } = await api();
    return await viewerApi.getBlob(scope, key);
  }

  /** Synchronous by contract (a URL is a string, not a request), so it cannot
   * await the dynamic import. `viewerApi.blobUrl` is pure string formatting
   * over `runtime.apiBase()`, mirrored here verbatim rather than imported so
   * the module keeps the isolation its header describes. */
  blobUrl(scope: ScopeUrl, key: string): string {
    return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/blobs/${encodeURIComponent(key)}`;
  }

  async requestDownloadUrl(scope: ScopeUrl, key: string): Promise<PresignedDownload> {
    const { viewerApi } = await api();
    return await viewerApi.requestDownloadUrl(scope, key);
  }

  async requestUploadUrl(scope: ScopeUrl, key: string, size?: number): Promise<PresignedUpload> {
    const { viewerApi } = await api();
    return await viewerApi.requestUploadUrl(scope, key, size);
  }

  async reportUploadProgress(scope: ScopeUrl, key: string, loaded: number, total: number): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.uploadProgress(scope, key, loaded, total);
  }

  async completeUpload(scope: ScopeUrl, key: string): Promise<{ key: string; size: number }> {
    const { viewerApi } = await api();
    return await viewerApi.completeUpload(scope, key);
  }

  async putBlob(
    scope: ScopeUrl,
    key: string,
    body: BodyInit,
    opts?: { onProgress?: UploadProgressHandler },
  ): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.putBlob(scope, key, body, opts);
  }
}

export class RESTFeaCapability implements FeaCapability {
  readonly transport: CapabilityTransport = "rest";

  supports(_verb: FeaVerb): boolean {
    return true;
  }

  async fetchManifest(scope: ScopeUrl, sourceKey: string, opts?: FeaManifestOptions): Promise<FeaManifest> {
    const { viewerApi } = await api();
    return await viewerApi.feaManifest(scope, sourceKey, opts);
  }
}

export class RESTConversionCapability implements ConversionCapability {
  readonly transport: CapabilityTransport = "rest";

  supports(_verb: ConversionVerb): boolean {
    return true;
  }

  async jobStatus(jobId: string): Promise<ConvertResponse> {
    const { viewerApi } = await api();
    return await viewerApi.convertStatus(jobId);
  }
}

export class RESTCatalogCapability implements CatalogCapability {
  readonly transport: CapabilityTransport = "rest";

  supports(_verb: CatalogVerb): boolean {
    return true;
  }

  // equipment types

  async listEquipmentTypes(scope: ScopeUrl): Promise<EquipmentTypeSummary[]> {
    const { viewerApi } = await api();
    return await viewerApi.listEquipmentTypes(scope);
  }

  async createEquipmentType(scope: ScopeUrl, name: string): Promise<EquipmentTypeDetail> {
    const { viewerApi } = await api();
    return await viewerApi.createEquipmentType(scope, name);
  }

  async getEquipmentType(scope: ScopeUrl, typeId: string): Promise<EquipmentTypeDetail> {
    const { viewerApi } = await api();
    return await viewerApi.getEquipmentType(scope, typeId);
  }

  async updateEquipmentType(
    scope: ScopeUrl,
    typeId: string,
    fields: CatalogEntryFields<EquipmentTypeDoc>,
    baseRevision: number,
  ): Promise<CatalogRevision> {
    const { viewerApi } = await api();
    return await viewerApi.updateEquipmentType(scope, typeId, fields, baseRevision);
  }

  async deleteEquipmentType(scope: ScopeUrl, typeId: string): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.deleteEquipmentType(scope, typeId);
  }

  async uploadEquipmentCad(
    scope: ScopeUrl,
    typeId: string,
    filename: string,
    data: Blob | ArrayBuffer,
  ): Promise<{ cad_key: string }> {
    const { viewerApi } = await api();
    return await viewerApi.uploadEquipmentCad(scope, typeId, filename, data);
  }

  async copyEquipmentCadFromScope(scope: ScopeUrl, typeId: string, sourceKey: string): Promise<{ cad_key: string }> {
    const { viewerApi } = await api();
    return await viewerApi.copyEquipmentCadFromScope(scope, typeId, sourceKey);
  }

  async inferEquipmentBbox(scope: ScopeUrl, typeId: string): Promise<CatalogJobHandle> {
    const { viewerApi } = await api();
    return await viewerApi.inferEquipmentBbox(scope, typeId);
  }

  // system templates

  async listSystemTemplates(scope: ScopeUrl): Promise<SystemTemplateSummary[]> {
    const { viewerApi } = await api();
    return await viewerApi.listSystemTemplates(scope);
  }

  async createSystemTemplate(scope: ScopeUrl, name: string): Promise<SystemTemplateDetail> {
    const { viewerApi } = await api();
    return await viewerApi.createSystemTemplate(scope, name);
  }

  async getSystemTemplate(scope: ScopeUrl, templateId: string): Promise<SystemTemplateDetail> {
    const { viewerApi } = await api();
    return await viewerApi.getSystemTemplate(scope, templateId);
  }

  async updateSystemTemplate(
    scope: ScopeUrl,
    templateId: string,
    fields: CatalogEntryFields<SystemTemplateDoc>,
    baseRevision: number,
  ): Promise<CatalogRevision> {
    const { viewerApi } = await api();
    return await viewerApi.updateSystemTemplate(scope, templateId, fields, baseRevision);
  }

  async deleteSystemTemplate(scope: ScopeUrl, templateId: string): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.deleteSystemTemplate(scope, templateId);
  }

  // procedural engines

  async createEngine(scope: ScopeUrl, name: string): Promise<ProceduralEngineDetail> {
    const { viewerApi } = await api();
    return await viewerApi.createProceduralEngine(scope, name);
  }

  async getEngine(scope: ScopeUrl, engineId: string): Promise<ProceduralEngineDetail> {
    const { viewerApi } = await api();
    return await viewerApi.getProceduralEngine(scope, engineId);
  }

  async updateEngine(
    scope: ScopeUrl,
    engineId: string,
    fields: CatalogEntryFields<ProceduralEngineDoc>,
    baseRevision: number,
  ): Promise<CatalogRevision> {
    const { viewerApi } = await api();
    return await viewerApi.updateProceduralEngine(scope, engineId, fields, baseRevision);
  }

  async deleteEngine(scope: ScopeUrl, engineId: string): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.deleteProceduralEngine(scope, engineId);
  }
}

export class RESTComponentsCapability implements ComponentsCapability {
  readonly transport: CapabilityTransport = "rest";

  supports(_verb: ComponentsVerb): boolean {
    return true;
  }

  async fetchSpecs(scope: ScopeUrl): Promise<ComponentSpecsResponse> {
    const { viewerApi } = await api();
    return await viewerApi.componentsSpecs({ scope });
  }
}

export class RESTMetricsCapability implements MetricsCapability {
  readonly transport: CapabilityTransport = "rest";

  supports(_verb: MetricsVerb): boolean {
    return true;
  }

  async recordViewLoad(scope: ScopeUrl, record: ViewLoadRecord): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.recordViewLoad(scope, record);
  }

  async recordRenderProfile(scope: ScopeUrl, record: RenderProfileRecord): Promise<void> {
    const { viewerApi } = await api();
    await viewerApi.recordRenderProfile(scope, record);
  }
}

export class RESTCapabilities implements ViewerCapabilities {
  readonly transport: CapabilityTransport = "rest";
  readonly stats: ModelStatsCapability = new RESTModelStatsCapability();
  readonly procedural: ProceduralModelCapability = new RESTProceduralModelCapability();
  // ---- seams 3 ----
  readonly files: FilesCapability = new RESTFilesCapability();
  readonly fea: FeaCapability = new RESTFeaCapability();
  readonly conversion: ConversionCapability = new RESTConversionCapability();
  readonly catalog: CatalogCapability = new RESTCatalogCapability();
  readonly components: ComponentsCapability = new RESTComponentsCapability();
  readonly metrics: MetricsCapability = new RESTMetricsCapability();
}
