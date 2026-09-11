// Websocket implementations of the viewer capabilities (`assembly.show()`).
//
// There is no HTTP API behind the websocket transport — adapy pushes a GLB over
// ws://localhost:8765 into a page served from disk. So a WS capability cannot
// "fetch" anything; it reads what the artifact itself carries.
//
// For the take-off that is `asset.extras.model_stats`, written by
// `ada.visit.scene_converter.SceneConverter` whenever the rendered source is an
// `ada.Part`/`Assembly` (see `RenderParams.embed_model_stats`). It is the exact
// same document `ada.topo_model.takeoff.model_takeoff` produces for the REST
// worker's `.stats.json` sidecar, so the panel renders it unchanged.
//
// The GLB loader hands it over via `adoptEmbeddedStats` and the store then asks
// for it back through `fetchStats` — the store never learns where it came from.
//
// The procedural document works the same way: `asset.extras.procedural_doc` is the same
// document the REST worker stores and `viewerApi.getProceduralModel` returns, so the
// cellbuilder's existing `loadFromDoc` consumes it unchanged and the procedural equipment and
// system panels render with no code of their own.

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
  type ProceduralModelResult,
  type ProceduralModelSource,
  type ProceduralRelocationResponse,
  type ProceduralResyncResult,
  type ProceduralSyncableCatalogKind,
  type ProceduralXlsxImportRequest,
  type ProceduralXlsxImportResponse,
  type StatsExportFormat,
  type ViewerCapabilities,
} from "./types";

export class WSModelStatsCapability implements ModelStatsCapability {
  readonly transport: CapabilityTransport = "ws";

  // Take-off of the most recently loaded model. Null once a model without one
  // is loaded, so the panel drops back to "not available" instead of showing
  // the previous model's numbers.
  private embedded: ModelStats | null = null;

  async fetchStats(_source: ModelStatsSource): Promise<ModelStatsResult> {
    if (!this.embedded) return { available: false };
    return { available: true, stats: this.embedded };
  }

  adoptEmbeddedStats(stats: ModelStats | null): boolean {
    this.embedded = stats ?? null;
    return true;
  }

  /** No backend to build the workbook. The panel hides the export button. */
  canExport(_source: ModelStatsSource): boolean {
    return false;
  }

  async exportStats(_source: ModelStatsSource, _fmt: StatsExportFormat, _tab?: string): Promise<void> {
    // Intentionally a no-op — guarded by `canExport`.
  }
}


export class WSProceduralModelCapability implements ProceduralModelCapability {
  readonly transport: CapabilityTransport = "ws";

  /** No save verb is implemented over the websocket transport yet, so an edit has nowhere to go.
   * Not "there is no backend" -- adapy is right there on the other end of this socket. See the
   * interface docstring and `docs/documents/ws_rest_parity.rst`. */
  readonly canEdit = false;

  // Document of the most recently loaded model, or null once a model without one is loaded, so
  // the panels do not go on describing the previous model's equipment.
  private embedded: ProceduralDoc | null = null;

  async fetchModel(_source: ProceduralModelSource): Promise<ProceduralModelResult> {
    if (!this.embedded) return { available: false };
    return { available: true, doc: this.embedded };
  }

  adoptEmbeddedModel(doc: ProceduralDoc | null): boolean {
    this.embedded = doc ?? null;
    return true;
  }

  // ---- Verbs with no websocket implementation yet -------------------------------------------
  //
  // Every method below is where a websocket verb plugs in, one at a time (the ws/REST parity
  // plan). The hook is `Comms.request()` (`utils/comms/wsRequests.ts`): build the flatbuffer
  // command with the request id it hands you, await the correlated reply, decode it into the
  // seam's result type. Until a verb exists its method throws `CapabilityUnavailableError` --
  // an honest, typed refusal rather than a silent empty result -- so a consumer can tell "the
  // server said no" from "there is no server verb". The cellbuilder store already tolerates a
  // failed catalog fetch (empty list + warning) and gates editing on `canEdit`; a verb landing
  // here is what should flip that flag, with no other change on the consumer side.
  //
  // Which of these SHOULD ever land is a separate question the parity plan answers: catalogs,
  // compile and preview are in-process Python a local adapy can serve; the per-scope catalog
  // sync/resync, relocations and the xlsx import/export are backed by the cloud database and
  // job queue and are expected to stay REST-only.

  private unavailable(verb: string): never {
    throw new CapabilityUnavailableError(verb, this.transport);
  }

  async listCatalog<K extends ProceduralCatalogKind>(_scope: string, kind: K): Promise<ProceduralCatalogs[K]> {
    return this.unavailable(`listCatalog(${kind})`);
  }

  async listBlueprints(_scope: string, _engine: string): Promise<ProceduralBlueprintOption[]> {
    return this.unavailable("listBlueprints");
  }

  async syncCatalogEntry(
    _scope: string,
    kind: ProceduralSyncableCatalogKind,
    _slug: string,
  ): Promise<ProceduralCatalogSyncResult> {
    return this.unavailable(`syncCatalogEntry(${kind})`);
  }

  async resyncEquipmentTypes(_scope: string): Promise<ProceduralResyncResult> {
    return this.unavailable("resyncEquipmentTypes");
  }

  async commitModel(
    _scope: string,
    _modelId: string,
    _doc: ProceduralDoc,
    _baseRevision: number,
  ): Promise<ProceduralCommitResult> {
    // SAVE_PROCEDURAL_MODEL is the first verb the parity plan schedules; when it lands here,
    // flip `canEdit` above in the same change.
    return this.unavailable("commitModel");
  }

  async compileModel(
    _scope: string,
    _modelId: string,
    _opts: ProceduralBuildOptions,
  ): Promise<ProceduralCompileResponse> {
    return this.unavailable("compileModel");
  }

  async previewModel(
    _scope: string,
    _modelId: string,
    _doc: unknown,
    _opts: ProceduralBuildOptions,
  ): Promise<ProceduralCompileResponse> {
    return this.unavailable("previewModel");
  }

  async jobStatus(_jobId: string): Promise<ConvertResponse> {
    return this.unavailable("jobStatus");
  }

  async fetchCompileLog(
    _scope: string,
    _modelId: string,
    _derivedKey: string,
    _runId?: string | null,
  ): Promise<ProceduralCompileLog> {
    return this.unavailable("fetchCompileLog");
  }

  async resolveEngine(_scope: string, _engineId: string): Promise<ProceduralEngineResolved> {
    return this.unavailable("resolveEngine");
  }

  async exportModel(
    _scope: string,
    _modelId: string,
    format: ProceduralExportFormat,
    _opts?: ProceduralExportOptions,
  ): Promise<ProceduralCompileResponse> {
    return this.unavailable(`exportModel(${format})`);
  }

  async downloadArtifact(_scope: string, _key: string, _suggestedName: string): Promise<void> {
    return this.unavailable("downloadArtifact");
  }

  async stageXlsxImport(_scope: string, _data: Blob | ArrayBuffer): Promise<ProceduralXlsxDetect> {
    return this.unavailable("stageXlsxImport");
  }

  async importXlsx(_scope: string, _body: ProceduralXlsxImportRequest): Promise<ProceduralXlsxImportResponse> {
    return this.unavailable("importXlsx");
  }

  async fetchImportedModel(_scope: string, _derivedKey: string): Promise<ProceduralModelDetail> {
    return this.unavailable("fetchImportedModel");
  }

  async proposeRelocations(_scope: string, _modelId: string): Promise<ProceduralRelocationResponse> {
    return this.unavailable("proposeRelocations");
  }

  async fetchRelocations(_scope: string, _key: string): Promise<ProceduralRelocationResult> {
    return this.unavailable("fetchRelocations");
  }

  async fetchEquipmentPreviewGlb(_scope: string, _typeId: string): Promise<ArrayBuffer | null> {
    return this.unavailable("fetchEquipmentPreviewGlb");
  }
}

export class WSCapabilities implements ViewerCapabilities {
  readonly transport: CapabilityTransport = "ws";
  readonly stats: ModelStatsCapability = new WSModelStatsCapability();
  readonly procedural: ProceduralModelCapability = new WSProceduralModelCapability();
}
