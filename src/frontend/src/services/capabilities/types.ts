// Transport-agnostic *capability* interfaces for the viewer.
//
// `utils/comms` already abstracts the two transports at the BYTE level: a
// `Comms` implementation moves serialized flatbuffer Messages, and `comms/index`
// picks `WSComms` or `RESTComms` at runtime. What it does not abstract is
// everything the viewer needs that is NOT a flatbuffer Message — the take-off,
// compile logs, blobs, exports. Those grew as `services/viewerApi.ts`, which is
// REST-only, so any feature built on it silently does not exist on the
// websocket (`assembly.show()`) path even when the data is right there.
//
// A capability is that missing layer: ONE consumer-facing method, two
// transport-specific implementations, runtime-selected exactly like `comms`.
// Consumers (stores, panels) depend on the interface and never learn which
// transport answered.
//
// Adding a capability:
//   1. declare its interface here and add it to `ViewerCapabilities`,
//   2. implement it in `rest_capabilities.ts` (usually delegating to
//      `viewerApi`) and in `ws_capabilities.ts`,
//   3. export both from `index.ts`'s runtime-selected singleton,
//   4. move consumers off the direct `viewerApi` call.
// `viewerApi` stays the REST implementation detail — the seam is what domain
// code is allowed to see. Migration is incremental and by design: only the
// capabilities that actually have to work on both transports need to move.

import type {
  ConvertResponse,
  DetailingEngineSummary,
  DetailingOptionsPayload,
  ProceduralBlueprintOption,
  ProceduralCellTypeOption,
  ProceduralCompileResponse,
  ProceduralDesignRulesetOption,
  ProceduralDoc,
  ProceduralEngineResolved,
  ProceduralEngineSummary,
  ProceduralModelDetail,
  ProceduralOpeningTypeOption,
  ProceduralRelocationResult,
  ProceduralSystemTypeOption,
  ProceduralTypeOption,
  ProceduralXlsxDetect,
} from "@/services/viewerApi";
import type { ModelStats } from "@/utils/stats/modelStats";

/** Thrown by a transport for a verb it has no implementation of.
 *
 * This is the honest answer, not a stand-in: the websocket transport has request correlation
 * (`Comms.request()`, `utils/comms/wsRequests.ts`) but no procedural verbs riding on it yet, so
 * every store method that needs a backend must fail visibly there rather than pretend. Consumers
 * that already tolerate a failed backend call (the cellbuilder store's catalog fetches fall back
 * to an empty list and warn) need no special handling; the ones that gate UI should read `canEdit`
 * instead of catching this. */
export class CapabilityUnavailableError extends Error {
  constructor(
    public readonly verb: string,
    public readonly transport: CapabilityTransport,
  ) {
    super(`${verb} is not available over the ${transport} transport`);
    this.name = "CapabilityUnavailableError";
  }
}

/** Thrown by `commitModel` when the stored model moved on since `baseRevision` was read.
 *
 * Optimistic concurrency is a property of the model store, not of HTTP, so the seam names it
 * rather than leaking the REST client's 409 to the cellbuilder. */
export class ProceduralCommitConflictError extends Error {
  constructor(
    public readonly modelId: string,
    public readonly baseRevision: number,
  ) {
    super(`commit of ${modelId} conflicts with a newer revision than ${baseRevision}`);
    this.name = "ProceduralCommitConflictError";
  }
}

/** Which transport answered. Consumers should not branch on this — it exists
 * for diagnostics and for tests that assert the right implementation was
 * selected. */
export type CapabilityTransport = "rest" | "ws";

/** Scope sentinel for a model that lives on local disk rather than in a per-scope store.
 *
 * `cellBuilderStore`'s `currentScopePart()` falls back to `"user:me"` when no real scope is
 * selected, which is the REST-oriented default and happens to be exactly what a websocket session
 * sees too (there is no `/api/me` to populate a scope there). Passing `"user:me"` in that case
 * would *work by accident* and quietly mean something real the day a local model is synced to a
 * server (see docs/documents/ws_rest_parity.rst, "scope strings"). This sentinel is what the
 * websocket transport passes instead: recognisable as "no real scope" on sight, and never produced
 * or interpreted by the REST path, so passing it there fails the same way any other unrecognised
 * scope segment would -- rejected by absence of support, not by a special-cased guard. */
export const LOCAL_MODEL_SCOPE = "local:disk";

/** Identifies the model whose take-off is wanted.
 *
 * REST fills all three (they address the `.stats.json` sidecar of a compiled
 * GLB). The websocket path has none of them — the model was pushed straight
 * into the viewer and the take-off rides inside the GLB — so all three are
 * nullable and the WS implementation ignores them entirely. */
export interface ModelStatsSource {
  scope?: string | null;
  modelId?: string | null;
  derivedKey?: string | null;
}

export interface ModelStatsResult {
  available: boolean;
  stats?: ModelStats | null;
}

export type StatsExportFormat = "xlsx" | "csv";

/** The viewer "Stats"/take-off capability. */
export interface ModelStatsCapability {
  readonly transport: CapabilityTransport;

  /** Resolve the take-off for `source`. Never throws for a model that simply
   * has none — it resolves to `{available:false}` so the panel can degrade to
   * its muted "take-off not available" state. */
  fetchStats(source: ModelStatsSource): Promise<ModelStatsResult>;

  /** Offer a take-off found embedded in a freshly-loaded GLB
   * (`asset.extras.model_stats`, written by `ada.visit.scene_converter`).
   *
   * Returns true if this transport is the one that sources stats that way, in
   * which case the caller should refresh the store. The REST implementation
   * returns false — the hosted viewer's authority is the server-side sidecar
   * (it can be refreshed and exported), so an embedded copy must not race it. */
  adoptEmbeddedStats(stats: ModelStats | null): boolean;

  /** Whether a server-rendered xlsx/csv export can be produced for `source`.
   * False on the websocket path: building the workbook is a backend job and
   * there is no backend. */
  canExport(source: ModelStatsSource): boolean;

  /** Download the take-off export. Only meaningful when `canExport` is true. */
  exportStats(source: ModelStatsSource, fmt: StatsExportFormat, tab?: string): Promise<void>;
}

/** Identifies the procedural model wanted, addressed the same way the take-off is.
 *
 * REST fills these to reach the stored model; the websocket path has none of them, because the
 * document rides inside the GLB that was pushed into the viewer. */
export interface ProceduralModelSource {
  scope?: string | null;
  modelId?: string | null;
}

export interface ProceduralModelResult {
  available: boolean;
  doc?: ProceduralDoc | null;
}

/** One entry in a local-disk model browser listing (``LIST_PROCEDURAL_MODELS`` --
 * docs/documents/ws_rest_parity.rst, step 6). ``contentHash`` is the same sha256 hex digest
 * ``commitModel``/``fetchModel`` traffic in, so a browser row can be compared against a hash
 * already held (e.g. the currently-open model's ``knownHashes`` entry) without a round trip.
 * ``modifiedAt`` is Unix milliseconds -- display/sort only, never a concurrency token. */
export interface ProceduralModelEntry {
  modelId: string;
  contentHash: string;
  modifiedAt: number;
  sizeBytes: number;
}

/** Verb names `supports` can be asked about -- one per write/build verb on
 * `ProceduralModelCapability`, named for the method it gates. Deliberately a
 * closed union rather than `string`: adding a verb here is the reminder to
 * decide what each transport says about it. */
export type ProceduralVerb =
  | "commitModel"
  | "compileModel"
  | "previewModel"
  | "resyncEquipmentTypes"
  | "syncCatalogEntry"
  | "proposeRelocations"
  | "importXlsx"
  | "exportModel"
  | "listModels";

/** The procedural model behind a compiled assembly.
 *
 * This is what the "Procedural equipment" and "Procedural system" panels read: which equipment a
 * clicked body belongs to, what space it stands in, its size, masses and rotation, and which
 * systems touch which of its ports. None of that is in the GLB's geometry, and all of it is in the
 * document the compiler was given.
 *
 * Those panels already existed and already worked -- over REST, because the only way to reach the
 * document was `viewerApi.getProceduralModel`. On the websocket path they rendered nothing at all,
 * which is exactly the failure this seam exists to stop. */
export interface ProceduralModelCapability {
  readonly transport: CapabilityTransport;

  /** Resolve the procedural document for `source`. Never throws for a model that simply has none
   * (an IFC import, a hand-built assembly) -- it resolves to `{available:false}`.
   *
   * This is the hook the websocket save/load verbs will plug into (see the ws/REST parity plan
   * in the docs); today it has NO consumer -- the embedded-document path goes through
   * `adoptEmbeddedModel` below, and the hosted viewer opens models through its own store. */
  fetchModel(source: ProceduralModelSource): Promise<ProceduralModelResult>;

  /** List the models this transport can `fetchModel` by id (LIST_PROCEDURAL_MODELS -- ws/REST
   * parity plan, step 6). Gated by `supports("listModels")`: the websocket transport lists the
   * local-disk directory `save`/`load` read and write; REST has no equivalent concept (a per-scope
   * model listing is a different, already-existing endpoint the panels reach some other way), so
   * its implementation is unreachable behind `supports` returning false rather than silently
   * returning an empty list. */
  listModels(scope: string): Promise<ProceduralModelEntry[]>;

  /** Offer a document found embedded in a freshly-loaded GLB
   * (`asset.extras.procedural_doc`, written by `ada.visit.scene_converter`).
   *
   * Returns true if this transport sources the model that way, in which case the caller should
   * load it into the cellbuilder store. REST returns false: the hosted viewer's authority is the
   * stored model, which can be edited and committed, and an embedded copy must not race it. */
  adoptEmbeddedModel(doc: ProceduralDoc | null): boolean;

  /** Whether the model this transport serves can be edited and committed back.
   *
   * False on the websocket path -- but note carefully what that does and does not mean. It is NOT
   * that nothing is listening: there is a live adapy process on the other end of the socket, and it
   * is what pushed this model into the viewer in the first place. It is that **no save verb is
   * implemented over the websocket transport yet**, so there is nowhere for an edit to go. That is a
   * gap in the protocol, not a property of the transport, and it is expected to close -- see
   * `docs/documents/ws_rest_parity.rst`.
   *
   * Consumers should therefore gate editing UI on this flag rather than on "is this the websocket
   * path", so that when the verb lands, flipping this to true restores those controls with no other
   * change. `CellBuilderPanel` does exactly that. */
  readonly canEdit: boolean;

  /** Whether `verb` has a working implementation on THIS transport, independent of `canEdit`.
   *
   * `canEdit` answers one question -- "is there anywhere at all to commit an edit" -- and it
   * flips for the FIRST write verb that lands (save). Every verb after that lands on its own
   * schedule (see docs/documents/ws_rest_parity.rst's migration list), so a control that calls a
   * *different* verb cannot infer "this works" from `canEdit` alone without either lying about
   * verbs that aren't there yet or reintroducing a second all-or-nothing flag next to it. `supports`
   * is the per-verb answer instead: REST supports every verb unconditionally (nothing here changes
   * REST behaviour); the websocket transport supports only the verbs it has actually implemented.
   * A control that would otherwise call a verb that only throws `CapabilityUnavailableError` should
   * gate on this rather than on `canEdit` or `transport`. */
  supports(verb: ProceduralVerb): boolean;

  // ---- Catalogs -----------------------------------------------------------------------------
  //
  // The dropdown catalogs the cellbuilder panel is built from. Over REST these are the per-scope
  // database catalog unioned with the code archetypes; the ws/REST parity plan notes a local
  // process serves the same lists from the in-process registries, so the seam keys them by kind
  // rather than by endpoint.

  /** List one catalog for `scope`. The result type follows `kind`. */
  listCatalog<K extends ProceduralCatalogKind>(scope: string, kind: K): Promise<ProceduralCatalogs[K]>;

  /** Blueprints are engine-scoped, so they take the engine the compile will dispatch to. */
  listBlueprints(scope: string, engine: string): Promise<ProceduralBlueprintOption[]>;

  /** Upsert one code archetype (`slug`) into the scope's database catalog. */
  syncCatalogEntry(scope: string, kind: ProceduralSyncableCatalogKind, slug: string): Promise<ProceduralCatalogSyncResult>;

  /** Upsert every equipment archetype into the scope's database catalog and report what moved. */
  resyncEquipmentTypes(scope: string): Promise<ProceduralResyncResult>;

  // ---- Model revisions ----------------------------------------------------------------------

  /** Commit `doc` as the next revision of `modelId`, given that the caller last read
   * `baseRevision`. Rejects with `ProceduralCommitConflictError` if the model moved on. */
  commitModel(scope: string, modelId: string, doc: ProceduralDoc, baseRevision: number): Promise<ProceduralCommitResult>;

  // ---- Build jobs ---------------------------------------------------------------------------
  //
  // Compile, preview and export all answer with a `ProceduralCompileResponse`: either the
  // artifact is already cached (`cached`, no `job_id`) or a job was queued whose progress the
  // caller polls with `jobStatus` until it reports the `derived_key` of the finished artifact.

  /** Build the COMMITTED revision of `modelId`. */
  compileModel(scope: string, modelId: string, opts: ProceduralBuildOptions): Promise<ProceduralCompileResponse>;

  /** Build the given (uncommitted) `doc` as an ephemeral preview -- no revision bump. */
  previewModel(scope: string, modelId: string, doc: unknown, opts: ProceduralBuildOptions): Promise<ProceduralCompileResponse>;

  /** Poll a queued job. */
  jobStatus(jobId: string): Promise<ConvertResponse>;

  /** The engine's compile log for a finished (or failed) build, addressed by artifact key or by
   * the run that produced it. Resolves to an empty text for a build that has none. */
  fetchCompileLog(scope: string, modelId: string, derivedKey: string, runId?: string | null): Promise<ProceduralCompileLog>;

  /** Resolve a non-builtin engine to something the in-browser compiler can load. */
  resolveEngine(scope: string, engineId: string): Promise<ProceduralEngineResolved>;

  // ---- Export -------------------------------------------------------------------------------

  /** Produce a downloadable artifact of the COMMITTED revision in `format`. Same job contract
   * as `compileModel`; fetch the result with `downloadArtifact`. */
  exportModel(scope: string, modelId: string, format: ProceduralExportFormat, opts?: ProceduralExportOptions): Promise<ProceduralCompileResponse>;

  /** Hand the artifact stored under `key` to the user as a file download named `suggestedName`. */
  downloadArtifact(scope: string, key: string, suggestedName: string): Promise<void>;

  // ---- Import -------------------------------------------------------------------------------

  /** Stage an uploaded workbook and read which engine (if any) it declares. */
  stageXlsxImport(scope: string, data: Blob | ArrayBuffer): Promise<ProceduralXlsxDetect>;

  /** Queue the import of a staged workbook as a new model. Poll with `jobStatus`. */
  importXlsx(scope: string, body: ProceduralXlsxImportRequest): Promise<ProceduralXlsxImportResponse>;

  /** Resolve the model an import job produced (its result artifact names the model). */
  fetchImportedModel(scope: string, derivedKey: string): Promise<ProceduralModelDetail>;

  // ---- Relocations --------------------------------------------------------------------------

  /** Ask for equipment relocation proposals for `modelId`. A null `job_id` means the analysis
   * was already cached under `derived_key`; otherwise poll with `jobStatus`. */
  proposeRelocations(scope: string, modelId: string): Promise<ProceduralRelocationResponse>;

  /** Read the proposals an analysis stored under `key`. */
  fetchRelocations(scope: string, key: string): Promise<ProceduralRelocationResult>;

  // ---- Equipment preview --------------------------------------------------------------------

  /** The CAD preview GLB (possibly gzipped) of the equipment type `typeId`, or null if the type
   * has none. Used by the cellbuilder controller to show real equipment shapes in the cells. */
  fetchEquipmentPreviewGlb(scope: string, typeId: string): Promise<ArrayBuffer | null>;
}

/** The typed catalogs `listCatalog` serves, keyed by the kind a consumer asks for. */
export interface ProceduralCatalogs {
  equipmentTypes: ProceduralTypeOption[];
  cellTypes: ProceduralCellTypeOption[];
  openingTypes: ProceduralOpeningTypeOption[];
  systemTypes: ProceduralSystemTypeOption[];
  designRulesets: ProceduralDesignRulesetOption[];
  engines: ProceduralEngineSummary[];
  detailingEngines: DetailingEngineSummary[];
}

export type ProceduralCatalogKind = keyof ProceduralCatalogs;

/** The catalogs whose entries can be upserted one at a time from a code archetype. */
export type ProceduralSyncableCatalogKind = "equipmentTypes" | "systemTypes";

export interface ProceduralCatalogSyncResult {
  id: string;
  slug: string;
  revision: number;
}

export interface ProceduralResyncResult {
  created: string[];
  updated: string[];
  unchanged: string[];
  skipped: string[];
  /** Per-slug human-readable "what changed" (created/updated slugs only). */
  changes: Record<string, string[]>;
}

export interface ProceduralCommitResult {
  id: string;
  revision: number;
}

export type ProceduralLod = "sim" | "detail";

export interface ProceduralBuildOptions {
  /** Recompile even if the artifact for this document is cached. */
  force?: boolean;
  lod?: ProceduralLod;
  /** Procedural engine slug; null/undefined lets the server pick its default. */
  engine?: string | null;
  /** Fabrication-detail engine slug applied after the structural build; "none" = structural only. */
  detailing?: string | null;
  detailingOptions?: DetailingOptionsPayload | null;
}

export interface ProceduralCompileLog {
  text: string;
  runId: string;
}

export type ProceduralExportFormat = "xlsx" | "ifc" | "gxml" | "gnx";

export interface ProceduralExportOptions {
  force?: boolean;
  /** xlsx only: the engine whose workbook layout to write. */
  engine?: string | null;
  /** ifc only: include CAD geometry. */
  cad?: boolean;
}

export interface ProceduralXlsxImportRequest {
  source_key: string;
  engine: string;
  name: string;
}

export interface ProceduralXlsxImportResponse {
  job_id: string;
  derived_key: string;
}

export interface ProceduralRelocationResponse {
  job_id: string | null;
  derived_key: string;
}

/** The runtime-selected capability set. One instance per transport; see
 * `index.ts`. */
export interface ViewerCapabilities {
  readonly transport: CapabilityTransport;
  readonly stats: ModelStatsCapability;
  readonly procedural: ProceduralModelCapability;
}
