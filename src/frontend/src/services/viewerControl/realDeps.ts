// The app's `ViewerControlDeps`: the stores and the scene the commands of ./commands.ts reach, and
// the UI's own functions for what they do (the joints review's `focusJoint`, the Clashes panel's
// cross-model select, the Detail models list's Load). Modules that pull three.js in are imported on
// first use, as the panels do.

import { clashCheckApi } from "@/services/api/clashCheck";
import { parseDetailStats, useClashCheckStore } from "@/state/clashCheckStore";
import { useDetailModelsStore } from "@/state/detailModelsStore";
import { useModelState } from "@/state/modelState";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { rootsForSource } from "@/utils/cellbuilder/structureNodes";
import { activeViewport, subscribeSelection, type ObjectRef } from "@/viewport/api/viewportApi";

import type { ViewerControlDeps } from "./commands";

/** The take-off's connections for `jointId`: a detail run names them `<spec>_<joint id>` (and
 *  `_<n>` after it for a second pairing at the same joint). */
export function connectionsOfJoint(names: readonly string[], jointId: string): string[] {
  return names.filter((n) => n.endsWith(`_${jointId}`) || new RegExp(`_${jointId}_\\d+$`).test(n));
}

export const realViewerControlDeps: ViewerControlDeps = {
  viewport: activeViewport,
  scope: () => (useScopeStore.getState().current ? scopeUrlPart(useScopeStore.getState().current) : null),
  loadedSources: () => [...useModelState.getState().loadedSourceNames],
  activeSource: () => useModelState.getState().loadedSourceName,
  clash: () => {
    const s = useClashCheckStore.getState();
    return {
      runKey: s.runKey,
      joints: s.result ? s.result.joints.length : null,
      selectedJoint: s.selectedJoint,
      hasJoint: (id) => !!s.result?.jointsById.has(id),
    };
  },
  openRun: (scope, runKey) => useClashCheckStore.getState().openRecentContactRun(scope, runKey),
  focusJoint: async (jointId, revealPanel) => {
    const { focusJoint } = await import("@/utils/scene/clashJointFocus");
    await focusJoint(jointId, { revealPanel });
  },
  selectNames: async (file, names) => {
    const { selectInOtherModel } = await import("@/utils/scene/crossModelSelect");
    await selectInOtherModel({ file, nodeNames: [...names] });
  },
  objectsNamed: async (file, name) => {
    const modelKey = rootsForSource(useTreeViewStore.getState().treeData, file).at(-1)?.model_key ?? null;
    if (!modelKey) return [];
    const { modelStore } = await import("@/state/model_worker/modelStore");
    const pairs = await modelStore.getDrawRangesByMemberNames(modelKey, [name]);
    return pairs.map(([node, id]): ObjectRef => ({ model: modelKey, id: String(id), node }));
  },
  listDetailModels: (scope) => clashCheckApi.listDetailModels(scope),
  openDetailModel: (scope, model) => useDetailModelsStore.getState().open(scope, model),
  detailConnections: async (scope, model, jointId) => {
    const stats = parseDetailStats(await clashCheckApi.getDetailStats(scope, model.stats_key));
    return connectionsOfJoint(stats.joints?.items.map((i) => i.name) ?? [], jointId);
  },
  savedGroups: async (scope) => {
    const { useSavedGroupsStore } = await import("@/state/savedGroupsStore");
    const store = useSavedGroupsStore;
    await store.getState().load(scope);
    const now = store.getState();
    if (now.scope !== scope) return [];
    if (now.error) throw new Error(`the saved groups could not be read: ${now.error}`);
    return now.groups.map((g) => ({ id: g.id, name: g.name }));
  },
  loadSavedGroup: async (scope, groupId) => {
    // The plugin API's `ctx.scene.loadSavedGroup`: the Scene panel's "Load into scene".
    const { liveGroupLoadEnv, loadSavedGroupFor } = await import("@/plugins/sceneGroupLoad");
    return loadSavedGroupFor(liveGroupLoadEnv(() => scope), groupId);
  },
  settle: async () => {
    const { requestRender } = await import("@/state/perfStore");
    requestRender();
    await new Promise((resolve) => setTimeout(resolve, 50));
  },
};

/** Calls `changed` whenever what a state report says may have changed: the selection, the loaded
 *  models, the scope, the open clash run or its cursor. */
export function watchViewerState(changed: () => void): () => void {
  const offs = [
    subscribeSelection(changed),
    useModelState.subscribe((s, p) => {
      if (s.loadedSourceNames !== p.loadedSourceNames || s.loadedSourceName !== p.loadedSourceName) changed();
    }),
    useScopeStore.subscribe((s, p) => {
      if (s.current !== p.current) changed();
    }),
    useClashCheckStore.subscribe((s, p) => {
      if (s.runKey !== p.runKey || s.selectedJoint !== p.selectedJoint || s.result !== p.result) changed();
    }),
  ];
  return () => offs.forEach((off) => off());
}
