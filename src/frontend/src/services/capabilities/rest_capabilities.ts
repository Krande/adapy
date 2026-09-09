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
  CapabilityTransport,
  ModelStatsCapability,
  ModelStatsResult,
  ModelStatsSource,
  StatsExportFormat,
  ViewerCapabilities,
} from "./types";

export class RESTModelStatsCapability implements ModelStatsCapability {
  readonly transport: CapabilityTransport = "rest";

  async fetchStats(source: ModelStatsSource): Promise<ModelStatsResult> {
    const { scope, modelId, derivedKey } = source;
    if (!scope || !modelId || !derivedKey) return { available: false };
    const { viewerApi } = await import("@/services/viewerApi");
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
    const { viewerApi } = await import("@/services/viewerApi");
    await viewerApi.downloadStatsExport(scope, modelId, derivedKey, fmt, tab);
  }
}

export class RESTCapabilities implements ViewerCapabilities {
  readonly transport: CapabilityTransport = "rest";
  readonly stats: ModelStatsCapability = new RESTModelStatsCapability();
}
