import { runtime } from "@/runtime/config";
import { RESTCapabilities } from "./rest_capabilities";
import { WSCapabilities } from "./ws_capabilities";
import type { ViewerCapabilities } from "./types";

export type {
  CapabilityTransport,
  ModelStatsCapability,
  ModelStatsResult,
  ModelStatsSource,
  ProceduralBuildOptions,
  ProceduralCatalogKind,
  ProceduralCatalogSyncResult,
  ProceduralCatalogs,
  ProceduralCommitResult,
  ProceduralCompileLog,
  ProceduralExportFormat,
  ProceduralExportOptions,
  ProceduralLod,
  ProceduralModelCapability,
  ProceduralModelEntry,
  ProceduralModelResult,
  ProceduralModelSource,
  ProceduralRelocationResponse,
  ProceduralResyncResult,
  ProceduralSyncableCatalogKind,
  ProceduralVerb,
  ProceduralXlsxImportRequest,
  ProceduralXlsxImportResponse,
  StatsExportFormat,
  ViewerCapabilities,
} from "./types";
export { CapabilityUnavailableError, LOCAL_MODEL_SCOPE, ProceduralCommitConflictError } from "./types";
export {
  RESTCapabilities,
  RESTModelStatsCapability,
  RESTProceduralModelCapability,
} from "./rest_capabilities";
export { WSCapabilities, WSModelStatsCapability, WSProceduralModelCapability } from "./ws_capabilities";

// Singleton capability set, selected the same way and by the same signal as the
// `comms` transport singleton (`utils/comms/index.ts`): WS for desktop / dev
// (`assembly.show()`), REST for the hosted viewer, which injects
// window.COMMS_MODE = "rest". Same artifact, runtime-selected implementation.
//
// Selection is deferred to first use rather than done at module evaluation:
// `runtime.isRestMode()` reads `window`, and this module sits under stores that
// are imported in plain-Node unit tests where no DOM exists. Importing a seam
// must not require a browser.
let selected: ViewerCapabilities | null = null;

export function getCapabilities(): ViewerCapabilities {
  if (selected === null) {
    selected = runtime.isRestMode() ? new RESTCapabilities() : new WSCapabilities();
  }
  return selected;
}

// Depend on `capabilities.<name>` from stores and panels — never on
// `viewerApi` directly for anything that must also work on the websocket path.
export const capabilities: ViewerCapabilities = {
  get transport() {
    return getCapabilities().transport;
  },
  get stats() {
    return getCapabilities().stats;
  },
  get procedural() {
    return getCapabilities().procedural;
  },
};
