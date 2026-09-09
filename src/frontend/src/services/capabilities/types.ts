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

import type { ProceduralDoc } from "@/services/viewerApi";
import type { ModelStats } from "@/utils/stats/modelStats";

/** Which transport answered. Consumers should not branch on this — it exists
 * for diagnostics and for tests that assert the right implementation was
 * selected. */
export type CapabilityTransport = "rest" | "ws";

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
   * (an IFC import, a hand-built assembly) -- it resolves to `{available:false}`. */
  fetchModel(source: ProceduralModelSource): Promise<ProceduralModelResult>;

  /** Offer a document found embedded in a freshly-loaded GLB
   * (`asset.extras.procedural_doc`, written by `ada.visit.scene_converter`).
   *
   * Returns true if this transport sources the model that way, in which case the caller should
   * load it into the cellbuilder store. REST returns false: the hosted viewer's authority is the
   * stored model, which can be edited and committed, and an embedded copy must not race it. */
  adoptEmbeddedModel(doc: ProceduralDoc | null): boolean;

  /** Whether the model this transport serves can be edited and committed back. False on the
   * websocket path -- the document came out of a GLB and there is no backend to commit to, so the
   * panels are read-only there. */
  readonly canEdit: boolean;
}

/** The runtime-selected capability set. One instance per transport; see
 * `index.ts`. */
export interface ViewerCapabilities {
  readonly transport: CapabilityTransport;
  readonly stats: ModelStatsCapability;
  readonly procedural: ProceduralModelCapability;
}
