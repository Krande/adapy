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
//
// `commitModel` is the exception: SAVE_PROCEDURAL_MODEL (ws/REST parity plan, step 3) is the
// first websocket verb that actually sends a command and awaits a correlated reply
// (`Comms.request()`, `utils/comms/wsRequests.ts`) rather than reading something the GLB carried.
// It still does not statically import `@/utils/comms` -- that index unconditionally pulls in
// `RESTComms`, which pulls in `services/auth/oidc`, which reads `sessionStorage` at module scope
// (see `rest_capabilities.ts`'s module comment for the REST-side mirror of this same problem). A
// capability module that merely gets IMPORTED must not crash a bare-node test for that reason
// alone, so the transport is resolved with a dynamic `import()` the one time `commitModel` needs
// it, exactly like `rest_capabilities.ts` does for `viewerApi`.

import * as flatbuffers from "flatbuffers";
import { CommandType } from "@/flatbuffers/commands/command-type";
import { TargetType } from "@/flatbuffers/commands/target-type";
import { ProceduralModelLoad } from "@/flatbuffers/server/procedural-model-load";
import { ProceduralModelSave } from "@/flatbuffers/server/procedural-model-save";
import { Server } from "@/flatbuffers/server/server";
import { Message } from "@/flatbuffers/wsock/message";
import type { ModelStats } from "@/utils/stats/modelStats";
import type { Comms } from "@/utils/comms";
import { ServerReplyError } from "@/utils/comms/wsRequests";
import { useWebsocketStatusStore } from "@/state/websocketStatusStore";
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
  LOCAL_MODEL_SCOPE,
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

// Mirrors `CONFLICT_ERROR_CODE` in `ada.comms.msg_handling.save_procedural_model` -- the wire
// contract for "the expected_content_hash you sent does not match what's on disk", carried on
// `base.Error.code` rather than sniffed out of the message text.
const SAVE_CONFLICT_ERROR_CODE = 409;

/** Serialize a SAVE_PROCEDURAL_MODEL command. `expectedContentHash` is omitted (not an empty
 * string) when null, so a first save for a `model_id` this session has no hash for yet reaches
 * the handler as "no concurrency check" rather than "expected an empty file". */
function buildSaveProceduralModelRequest(
  instanceId: number,
  requestId: string,
  modelId: string,
  docJson: string,
  expectedContentHash: string | null,
): Uint8Array {
  const builder = new flatbuffers.Builder(1024);
  const modelIdOffset = builder.createString(modelId);
  const docJsonOffset = builder.createString(docJson);
  const expectedHashOffset = expectedContentHash ? builder.createString(expectedContentHash) : 0;

  ProceduralModelSave.startProceduralModelSave(builder);
  ProceduralModelSave.addModelId(builder, modelIdOffset);
  ProceduralModelSave.addDocJson(builder, docJsonOffset);
  if (expectedHashOffset) ProceduralModelSave.addExpectedContentHash(builder, expectedHashOffset);
  const saveOffset = ProceduralModelSave.endProceduralModelSave(builder);

  Server.startServer(builder);
  Server.addSaveProceduralModel(builder, saveOffset);
  const serverOffset = Server.endServer(builder);

  const requestIdOffset = builder.createString(requestId);

  Message.startMessage(builder);
  Message.addInstanceId(builder, instanceId);
  Message.addCommandType(builder, CommandType.SAVE_PROCEDURAL_MODEL);
  Message.addTargetGroup(builder, TargetType.SERVER);
  Message.addClientType(builder, TargetType.WEB);
  Message.addServer(builder, serverOffset);
  Message.addRequestId(builder, requestIdOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array();
}

/** Serialize a LOAD_PROCEDURAL_MODEL command for `modelId`. */
function buildLoadProceduralModelRequest(instanceId: number, requestId: string, modelId: string): Uint8Array {
  const builder = new flatbuffers.Builder(512);
  const modelIdOffset = builder.createString(modelId);

  ProceduralModelLoad.startProceduralModelLoad(builder);
  ProceduralModelLoad.addModelId(builder, modelIdOffset);
  const loadOffset = ProceduralModelLoad.endProceduralModelLoad(builder);

  Server.startServer(builder);
  Server.addLoadProceduralModel(builder, loadOffset);
  const serverOffset = Server.endServer(builder);

  const requestIdOffset = builder.createString(requestId);

  Message.startMessage(builder);
  Message.addInstanceId(builder, instanceId);
  Message.addCommandType(builder, CommandType.LOAD_PROCEDURAL_MODEL);
  Message.addTargetGroup(builder, TargetType.SERVER);
  Message.addClientType(builder, TargetType.WEB);
  Message.addServer(builder, serverOffset);
  Message.addRequestId(builder, requestIdOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array();
}

/** Serialize a LIST_PROCEDURAL_MODELS command. No request payload -- like LIST_PROCEDURES, the
 * command_type alone selects the handler (see `commands.fbs`). */
function buildListProceduralModelsRequest(instanceId: number, requestId: string): Uint8Array {
  const builder = new flatbuffers.Builder(256);
  const requestIdOffset = builder.createString(requestId);

  Message.startMessage(builder);
  Message.addInstanceId(builder, instanceId);
  Message.addCommandType(builder, CommandType.LIST_PROCEDURAL_MODELS);
  Message.addTargetGroup(builder, TargetType.SERVER);
  Message.addClientType(builder, TargetType.WEB);
  Message.addRequestId(builder, requestIdOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array();
}

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

  // The verbs actually implemented over the websocket. `supports` reads this; a verb landing
  // just means adding its name here (and, for the FIRST one, flipping `canEdit` below -- every
  // verb after that is independent of it, see the `supports` doc in types.ts).
  private static readonly SUPPORTED_VERBS: ReadonlySet<ProceduralVerb> = new Set<ProceduralVerb>([
    "commitModel",
    "listModels",
  ]);

  // Injectable for tests (a fake transport exposing just `request`/`getInstanceId`); production
  // code leaves this unset and resolves the real `comms` singleton lazily -- see `resolveWs`.
  constructor(private readonly wsOverride?: Pick<Comms, "request" | "getInstanceId">) {}

  /** Whether the socket is connected, taken as "yes, there is somewhere to commit to".
   *
   * The alternative the parity plan floats is a capability probe -- ask the process what it
   * supports and wait for an answer before trusting `canEdit`. That buys nothing here: a probe
   * would still need the socket connected to answer at all, and `commitModel` itself fails no
   * worse (a rejected `comms.request`) the moment the socket drops mid-edit than a stale "yes"
   * from an earlier probe would. Connectivity is the simpler signal and it is never wrong in a
   * way a probe's answer would not also eventually be wrong in.
   *
   * Read from `useWebsocketStatusStore` rather than a transport's own `isConnected()` so this
   * stays a plain, synchronous zustand read -- no import of `@/utils/comms` needed just to
   * answer `canEdit` (see the module comment on why that import is deferred). `WSComms` sets
   * this store's `connected` flag on exactly the same open/close events `isConnected()` would
   * read off the socket, so the two are never out of step. */
  get canEdit(): boolean {
    return useWebsocketStatusStore.getState().connected;
  }

  /** Resolve the transport to send on. Deferred (dynamic `import()`) rather than a static import
   * of the `comms` singleton -- see the module comment. Only called once `canEdit` (above) has
   * already established there is a live socket to use. */
  private async resolveWs(): Promise<Pick<Comms, "request" | "getInstanceId">> {
    if (this.wsOverride) return this.wsOverride;
    const { comms } = await import("@/utils/comms");
    return comms;
  }

  // Document of the most recently loaded model, or null once a model without one is loaded, so
  // the panels do not go on describing the previous model's equipment.
  private embedded: ProceduralDoc | null = null;

  // The content hash SAVE_PROCEDURAL_MODEL last reported for a given model_id, this session only.
  // Threaded back as `expected_content_hash` on the NEXT save of the same id, so sequential saves
  // within one running viewer get real optimistic concurrency; a model_id this map has never seen
  // (first save, or a fresh page load) saves unconditionally. Not persisted and not the numeric
  // `revision` the interface carries -- see `commitModel`'s docstring for why.
  private readonly knownHashes = new Map<string, string>();

  /** Resolve `source.modelId` off local disk via LOAD_PROCEDURAL_MODEL when given one; otherwise
   * (or on any failure -- disconnected socket, unknown id) fall back to whatever document the most
   * recently loaded GLB carried, exactly like before this verb existed. This is deliberately never
   * gated on `supports("fetchModel")` -- there is no such verb name, because `fetchModel` never
   * throws `CapabilityUnavailableError`: a model that cannot be resolved by either path is reported
   * absent, the same contract the REST implementation gives for a model with no procedural
   * provenance.
   *
   * A successful load also seeds `knownHashes` for `modelId` with the hash the server reports, so
   * a `commitModel` that follows sends real optimistic concurrency instead of saving blind (as it
   * would for a `model_id` this session has never touched). */
  async fetchModel(source: ProceduralModelSource): Promise<ProceduralModelResult> {
    const modelId = source.modelId;
    if (modelId) {
      try {
        const ws = await this.resolveWs();
        const reply = await ws.request((requestId) =>
          buildLoadProceduralModelRequest(ws.getInstanceId(), requestId, modelId),
        );
        const loaded = reply.serverReply()?.loadProceduralModel();
        const docJson = loaded?.docJson();
        if (docJson) {
          const doc = JSON.parse(docJson) as ProceduralDoc;
          const contentHash = loaded?.contentHash();
          if (contentHash) this.knownHashes.set(modelId, contentHash);
          return { available: true, doc };
        }
      } catch {
        // Disconnected, unknown model_id, or a malformed doc -- fall through to the embedded
        // document below rather than surface an error from what is documented as a
        // never-throws lookup.
      }
    }
    if (!this.embedded) return { available: false };
    return { available: true, doc: this.embedded };
  }

  /** LIST_PROCEDURAL_MODELS: every document saved under the local model directory. `scope` is
   * accepted for interface parity with the REST signature and otherwise ignored, the same way
   * `commitModel` ignores it -- local disk has no scopes to address.
   *
   * Disconnected (`!canEdit`) throws `CapabilityUnavailableError` up front, the same typed refusal
   * `commitModel` gives for the same reason, rather than letting `comms.request` reject with a
   * generic transport error a browser UI would have to pattern-match. */
  async listModels(_scope: string): Promise<ProceduralModelEntry[]> {
    if (!this.canEdit) {
      throw new CapabilityUnavailableError("listModels", this.transport);
    }
    const ws = await this.resolveWs();
    const reply = await ws.request((requestId) => buildListProceduralModelsRequest(ws.getInstanceId(), requestId));
    const listed = reply.serverReply()?.listProceduralModels();
    const count = listed?.entriesLength() ?? 0;
    const entries: ProceduralModelEntry[] = [];
    for (let i = 0; i < count; i++) {
      const entry = listed!.entries(i);
      if (!entry) continue;
      const modelId = entry.modelId();
      if (!modelId) continue;
      entries.push({
        modelId,
        contentHash: entry.contentHash() ?? "",
        modifiedAt: Number(entry.modifiedAt()),
        sizeBytes: Number(entry.sizeBytes()),
      });
    }
    return entries;
  }

  adoptEmbeddedModel(doc: ProceduralDoc | null): boolean {
    this.embedded = doc ?? null;
    return true;
  }

  supports(verb: ProceduralVerb): boolean {
    return WSProceduralModelCapability.SUPPORTED_VERBS.has(verb);
  }

  /** Save `doc` to local disk under `modelId` (SAVE_PROCEDURAL_MODEL).
   *
   * `scope` is expected to be `LOCAL_MODEL_SCOPE` (`cellBuilderStore.currentScopePart()` resolves
   * it that way over this transport) and is otherwise ignored -- local disk has no scopes to
   * address. `baseRevision` is likewise not sent: the wire protocol's real concurrency token is a
   * content hash, not the numeric revision this interface carries for REST's sake, so this
   * implementation tracks hashes itself (`knownHashes`) and reports revision `0` always rather
   * than inventing a numeric encoding of a hash that would mean nothing to REST's `r{revision}`
   * display. A conflict is still real and still surfaces as `ProceduralCommitConflictError` --
   * it is only the NUMBER in that error that is not meaningful here.
   *
   * Disconnected (`!canEdit`) throws `CapabilityUnavailableError` rather than attempting the send
   * and letting `comms.request` reject on its own -- the same typed refusal every other
   * unimplemented verb gives, for the same reason: a consumer should be able to tell "there is
   * nowhere to send this" from "the send failed" without parsing an error string. */
  async commitModel(
    scope: string,
    modelId: string,
    doc: ProceduralDoc,
    baseRevision: number,
  ): Promise<ProceduralCommitResult> {
    if (!this.canEdit) {
      throw new CapabilityUnavailableError("commitModel", this.transport);
    }
    if (scope !== LOCAL_MODEL_SCOPE) {
      // Not fatal -- the local disk enforces no scopes regardless -- but worth knowing about,
      // since it means some caller is not going through `currentScopePart()`'s ws branch.
      console.warn(`commitModel(ws): expected the local scope sentinel "${LOCAL_MODEL_SCOPE}", got "${scope}"`);
    }

    const ws = await this.resolveWs();
    const docJson = JSON.stringify(doc);
    const expectedHash = this.knownHashes.get(modelId) ?? null;

    let reply: Message;
    try {
      reply = await ws.request((requestId) =>
        buildSaveProceduralModelRequest(ws.getInstanceId(), requestId, modelId, docJson, expectedHash),
      );
    } catch (e) {
      if (e instanceof ServerReplyError) {
        const error = e.reply.serverReply()?.error();
        if (error?.code() === SAVE_CONFLICT_ERROR_CODE) {
          throw new ProceduralCommitConflictError(modelId, baseRevision);
        }
        throw new Error(error?.message() || e.message);
      }
      throw e;
    }

    const saved = reply.serverReply()?.saveProceduralModel();
    const newHash = saved?.contentHash() ?? "";
    this.knownHashes.set(modelId, newHash);
    return { id: saved?.modelId() || modelId, revision: 0 };
  }

  // ---- Verbs with no websocket implementation yet -------------------------------------------
  //
  // Every method below is where a websocket verb plugs in, one at a time (the ws/REST parity
  // plan). The hook is `Comms.request()` (`utils/comms/wsRequests.ts`): build the flatbuffer
  // command with the request id it hands you, await the correlated reply, decode it into the
  // seam's result type. Until a verb exists its method throws `CapabilityUnavailableError` --
  // an honest, typed refusal rather than a silent empty result -- so a consumer can tell "the
  // server said no" from "there is no server verb". The cellbuilder store already tolerates a
  // failed catalog fetch (empty list + warning); a control that does NOT tolerate that failure
  // should gate on `supports()` (above) instead of calling the verb blind.
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

// =============================================================================
// Store- and scene-layer capabilities (seams 3).
//
// None of these verbs has a websocket implementation yet. The wire protocol
// (`CommandType`) can list the server's files and push one whole file into the
// scene (LIST_FILE_OBJECTS / VIEW_FILE_OBJECT); it cannot serve a blob by key,
// bake an FEA manifest, poll a job, edit a catalog or take a telemetry record.
// So every `supports()` below is false and every verb refuses with
// `CapabilityUnavailableError` -- the same typed refusal the procedural verbs
// give, for the same reason. The scene handlers that used to branch on
// `runtime.isRestMode()` now ask `supports()` and keep their websocket-side
// behaviour (the VIEW_FILE_OBJECT flow) in the other branch. A verb landing
// here means adding it to the class's `SUPPORTED_VERBS` and sending it through
// `Comms.request()` exactly as `commitModel` does above.
// =============================================================================

function refuse(verb: string): never {
  throw new CapabilityUnavailableError(verb, "ws");
}

export class WSFilesCapability implements FilesCapability {
  readonly transport: CapabilityTransport = "ws";
  private static readonly SUPPORTED_VERBS: ReadonlySet<FilesVerb> = new Set<FilesVerb>();

  supports(verb: FilesVerb): boolean {
    return WSFilesCapability.SUPPORTED_VERBS.has(verb);
  }

  async fetchBlob(_scope: ScopeUrl, _key: string): Promise<ArrayBuffer> {
    return refuse("fetchBlob");
  }

  blobUrl(_scope: ScopeUrl, _key: string): string {
    return refuse("blobUrl");
  }

  async requestDownloadUrl(_scope: ScopeUrl, _key: string): Promise<PresignedDownload> {
    return refuse("requestDownloadUrl");
  }

  async requestUploadUrl(_scope: ScopeUrl, _key: string, _size?: number): Promise<PresignedUpload> {
    return refuse("requestUploadUrl");
  }

  async reportUploadProgress(_scope: ScopeUrl, _key: string, _loaded: number, _total: number): Promise<void> {
    return refuse("reportUploadProgress");
  }

  async completeUpload(_scope: ScopeUrl, _key: string): Promise<{ key: string; size: number }> {
    return refuse("completeUpload");
  }

  async putBlob(
    _scope: ScopeUrl,
    _key: string,
    _body: BodyInit,
    _opts?: { onProgress?: UploadProgressHandler },
  ): Promise<void> {
    return refuse("putBlob");
  }
}

export class WSFeaCapability implements FeaCapability {
  readonly transport: CapabilityTransport = "ws";
  private static readonly SUPPORTED_VERBS: ReadonlySet<FeaVerb> = new Set<FeaVerb>();

  supports(verb: FeaVerb): boolean {
    return WSFeaCapability.SUPPORTED_VERBS.has(verb);
  }

  async fetchManifest(_scope: ScopeUrl, _sourceKey: string, _opts?: FeaManifestOptions): Promise<FeaManifest> {
    return refuse("fetchManifest");
  }
}

export class WSConversionCapability implements ConversionCapability {
  readonly transport: CapabilityTransport = "ws";
  private static readonly SUPPORTED_VERBS: ReadonlySet<ConversionVerb> = new Set<ConversionVerb>();

  supports(verb: ConversionVerb): boolean {
    return WSConversionCapability.SUPPORTED_VERBS.has(verb);
  }

  async jobStatus(_jobId: string): Promise<ConvertResponse> {
    return refuse("jobStatus");
  }
}

export class WSCatalogCapability implements CatalogCapability {
  readonly transport: CapabilityTransport = "ws";
  private static readonly SUPPORTED_VERBS: ReadonlySet<CatalogVerb> = new Set<CatalogVerb>();

  supports(verb: CatalogVerb): boolean {
    return WSCatalogCapability.SUPPORTED_VERBS.has(verb);
  }

  async listEquipmentTypes(_scope: ScopeUrl): Promise<EquipmentTypeSummary[]> {
    return refuse("listEquipmentTypes");
  }

  async createEquipmentType(_scope: ScopeUrl, _name: string): Promise<EquipmentTypeDetail> {
    return refuse("createEquipmentType");
  }

  async getEquipmentType(_scope: ScopeUrl, _typeId: string): Promise<EquipmentTypeDetail> {
    return refuse("getEquipmentType");
  }

  async updateEquipmentType(
    _scope: ScopeUrl,
    _typeId: string,
    _fields: CatalogEntryFields<EquipmentTypeDoc>,
    _baseRevision: number,
  ): Promise<CatalogRevision> {
    return refuse("updateEquipmentType");
  }

  async deleteEquipmentType(_scope: ScopeUrl, _typeId: string): Promise<void> {
    return refuse("deleteEquipmentType");
  }

  async uploadEquipmentCad(
    _scope: ScopeUrl,
    _typeId: string,
    _filename: string,
    _data: Blob | ArrayBuffer,
  ): Promise<{ cad_key: string }> {
    return refuse("uploadEquipmentCad");
  }

  async copyEquipmentCadFromScope(_scope: ScopeUrl, _typeId: string, _sourceKey: string): Promise<{ cad_key: string }> {
    return refuse("copyEquipmentCadFromScope");
  }

  async inferEquipmentBbox(_scope: ScopeUrl, _typeId: string): Promise<CatalogJobHandle> {
    return refuse("inferEquipmentBbox");
  }

  async listSystemTemplates(_scope: ScopeUrl): Promise<SystemTemplateSummary[]> {
    return refuse("listSystemTemplates");
  }

  async createSystemTemplate(_scope: ScopeUrl, _name: string): Promise<SystemTemplateDetail> {
    return refuse("createSystemTemplate");
  }

  async getSystemTemplate(_scope: ScopeUrl, _templateId: string): Promise<SystemTemplateDetail> {
    return refuse("getSystemTemplate");
  }

  async updateSystemTemplate(
    _scope: ScopeUrl,
    _templateId: string,
    _fields: CatalogEntryFields<SystemTemplateDoc>,
    _baseRevision: number,
  ): Promise<CatalogRevision> {
    return refuse("updateSystemTemplate");
  }

  async deleteSystemTemplate(_scope: ScopeUrl, _templateId: string): Promise<void> {
    return refuse("deleteSystemTemplate");
  }

  async createEngine(_scope: ScopeUrl, _name: string): Promise<ProceduralEngineDetail> {
    return refuse("createEngine");
  }

  async getEngine(_scope: ScopeUrl, _engineId: string): Promise<ProceduralEngineDetail> {
    return refuse("getEngine");
  }

  async updateEngine(
    _scope: ScopeUrl,
    _engineId: string,
    _fields: CatalogEntryFields<ProceduralEngineDoc>,
    _baseRevision: number,
  ): Promise<CatalogRevision> {
    return refuse("updateEngine");
  }

  async deleteEngine(_scope: ScopeUrl, _engineId: string): Promise<void> {
    return refuse("deleteEngine");
  }
}

export class WSComponentsCapability implements ComponentsCapability {
  readonly transport: CapabilityTransport = "ws";
  private static readonly SUPPORTED_VERBS: ReadonlySet<ComponentsVerb> = new Set<ComponentsVerb>();

  supports(verb: ComponentsVerb): boolean {
    return WSComponentsCapability.SUPPORTED_VERBS.has(verb);
  }

  async fetchSpecs(_scope: ScopeUrl): Promise<ComponentSpecsResponse> {
    return refuse("fetchSpecs");
  }
}

export class WSMetricsCapability implements MetricsCapability {
  readonly transport: CapabilityTransport = "ws";
  private static readonly SUPPORTED_VERBS: ReadonlySet<MetricsVerb> = new Set<MetricsVerb>();

  supports(verb: MetricsVerb): boolean {
    return WSMetricsCapability.SUPPORTED_VERBS.has(verb);
  }

  async recordViewLoad(_scope: ScopeUrl, _record: ViewLoadRecord): Promise<void> {
    return refuse("recordViewLoad");
  }

  async recordRenderProfile(_scope: ScopeUrl, _record: RenderProfileRecord): Promise<void> {
    return refuse("recordRenderProfile");
  }
}

export class WSCapabilities implements ViewerCapabilities {
  readonly transport: CapabilityTransport = "ws";
  readonly stats: ModelStatsCapability = new WSModelStatsCapability();
  readonly procedural: ProceduralModelCapability = new WSProceduralModelCapability();
  // ---- seams 3 ----
  readonly files: FilesCapability = new WSFilesCapability();
  readonly fea: FeaCapability = new WSFeaCapability();
  readonly conversion: ConversionCapability = new WSConversionCapability();
  readonly catalog: CatalogCapability = new WSCatalogCapability();
  readonly components: ComponentsCapability = new WSComponentsCapability();
  readonly metrics: MetricsCapability = new WSMetricsCapability();
}
