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
import type { ProceduralDoc } from "@/services/viewerApi";
import type {
  CapabilityTransport,
  ModelStatsCapability,
  ModelStatsResult,
  ModelStatsSource,
  ProceduralModelCapability,
  ProceduralModelResult,
  ProceduralModelSource,
  StatsExportFormat,
  ViewerCapabilities,
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
}

export class WSCapabilities implements ViewerCapabilities {
  readonly transport: CapabilityTransport = "ws";
  readonly stats: ModelStatsCapability = new WSModelStatsCapability();
  readonly procedural: ProceduralModelCapability = new WSProceduralModelCapability();
}
