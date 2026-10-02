// The LIVE half of the clash check's missing-geometry step (`runGeometryAwareCheckFlow` in
// `./clashCheckStore`): the plan route, the provider's declared node request, who the user is, the
// published tree the members are found in, and the same request-job deps "Request geometry…" uses.
//
// A module of its own, imported by the store on first use, because these reach the asset browser,
// the plugin listing and a component module -- none of which a check without a geometry provider,
// or a test of the store, should load.

import { requestDeps } from "@/components/asset_browser/RequestCollection";
import { assetsApi } from "@/services/api/assets";
import { clashCheckApi } from "@/services/api/clashCheck";
import type { ScopeUrl } from "@/services/api/client";
import { pluginsApi } from "@/services/api/plugins";
import { sourceNodesApi } from "@/services/api/sourceNodes";
import { assetProviderCollections } from "@/services/assetScopeCollections";
import { loaderFor } from "@/state/assetBrowserLoader";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";
import type { GeometryCheckFlowDeps } from "@/state/clashCheckStore";
import { useMeStore } from "@/state/meStore";
import { resolveElementRows } from "@/utils/groups/groupAssetRows";

export async function geometryRequestDeps(
  scope: string,
  onStage: (stage: string | null) => void,
): Promise<
  Pick<GeometryCheckFlowDeps, "geometryPlan" | "nodeRequestFor" | "isAdmin" | "resolveRows" | "request" | "afterRequest">
> {
  return {
    geometryPlan: (s, target, provider) =>
      clashCheckApi.geometryPlan(s as ScopeUrl, { target, geometryProvider: provider }),
    async nodeRequestFor(provider) {
      const plugins = await pluginsApi.listBackendPlugins().catch(() => ({ plugins: [] as unknown[] }));
      return assetProviderCollections(plugins.plugins ?? []).find((p) => p.providerId === provider)?.nodeRequest ?? null;
    },
    isAdmin: useMeStore.getState().isAdmin,
    resolveRows: (s, members) => resolveElementRows(s, members),
    // Jobs go to the global toast, exactly as the saved group's "Request geometry…" sends them.
    request: requestDeps((stage) => onStage(stage)),
    async afterRequest() {
      // The new publishes reach the Sources tree, so its Load buttons enable.
      try {
        await loaderFor(useAssetBrowserStore, assetsApi, sourceNodesApi).refresh(scope);
      } catch {
        // The tree re-reads on its own next time it is opened.
      }
    },
  };
}
