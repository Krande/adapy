/**
 * The viewer's own side of a scene state: `liveSceneStateDeps` puts one back (`./applySceneState`),
 * `captureSceneState` takes one of what the scene shows now.
 *
 * Loaded on use (it reaches the loaders, the camera and the clash store): the stores that apply a
 * state import it lazily, so their node tests never load three.js.
 */

import {clashCheckApi} from "@/services/api/clashCheck";
import {contactRunsApi, CONTACT_ORIGIN} from "@/services/clash/contactRuns";
import {mergeProducedJoints, parseDetailStats, parseClashResult, useClashCheckStore, contactRunToResult, type ClashResult} from "@/state/clashCheckStore";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {isLocalSource} from "@/utils/groups/localSources";
import type {GroupMember} from "@/utils/groups/savedGroups";
import {applySceneState, type ApplyOptions, type ApplyOutcome, type SceneStateDeps} from "./applySceneState";
import {missingNote, type RecordedClash, type RecordedDetail, type RecordedModel, type SceneRecord} from "./sceneRecord";

/** The members the last state put in the scene, and under what name: a state saved afterwards keeps
 *  them, as they are not models a reload can load again by themselves. */
let lastMembers: {name: string; members: readonly GroupMember[]} | null = null;

/** Put the check `clash` on the Clashes tab as its run left it: its target, then its result. */
export async function restoreClashCheck(scope: string, clash: RecordedClash, result?: ClashResult): Promise<void> {
    const read =
        result ??
        (clash.runKey
            ? contactRunToResult(await contactRunsApi.getRun(scope, clash.runKey), clash.runKey)
            : parseClashResult(await clashCheckApi.getClashResult(scope, clash.resultKey as string)));
    const store = useClashCheckStore.getState();
    const t = clash.target;
    if (t.kind === "file") store.setSource(t.sourceKey, t.sourceKey);
    else store.setAssetTarget(t.kind === "group" ? `group ${t.name}` : (t.node ?? t.subject), t);
    useClashCheckStore.setState({
        result: read,
        derivedKey: clash.runKey ? null : (clash.resultKey ?? null),
        runKey: clash.runKey ?? null,
        cached: true,
        error: null,
        selectedGroup: null,
        selectedJoints: [],
        selectedJoint: null,
        ...(clash.runKey ? {checker: CONTACT_ORIGIN} : {}),
    });
    const {startClashMarkerSync} = await import("@/utils/scene/clashJointMarkers");
    startClashMarkerSync();
}

/** Overlay a detail model (unless it is in the scene) and make it the active one, its joints listed. */
export async function openDetailModel(scope: string, detail: RecordedDetail): Promise<void> {
    if (!useModelState.getState().loadedSourceNames.has(detail.glbKey)) {
        const {overlay_file_in_scene} = await import("@/utils/scene/handlers/overlay_file_in_scene");
        await overlay_file_in_scene(detail.glbKey, detail.glbKey, {scope});
        requestRender();
    }
    let stats = parseDetailStats(null);
    try {
        stats = parseDetailStats(await clashCheckApi.getDetailStats(scope, detail.statsKey));
    } catch {
        // The geometry is loaded; a take-off that cannot be read leaves the produced list as it was.
    }
    useClashCheckStore.setState((s) => ({
        producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
        producedSkipped: [...s.producedSkipped, ...stats.skipped.filter((x) => !s.producedSkipped.includes(x))],
        detailDerivedKey: detail.statsKey,
        detailGlbKey: detail.glbKey,
        detailSpec: detail.spec ?? null,
        detailError: null,
    }));
}

export const liveSceneStateDeps: SceneStateDeps = {
    loaded: () => useModelState.getState().loadedSourceNames,
    async loadModel(_scope, m: RecordedModel) {
        const {reloadRecordedModel} = await import("./sceneRestore");
        return reloadRecordedModel(m);
    },
    async loadMembers(scope, name, members) {
        const [{liveGroupLoadEnv}, {groupLoadNote}] = await Promise.all([
            import("@/plugins/sceneGroupLoad"),
            import("@/utils/groups/groupLoadNote"),
        ]);
        lastMembers = {name, members};
        const out = await liveGroupLoadEnv(() => scope).load({id: `scene-state:${name}`, name, created_at: "", members}, scope);
        return out.failed.length ? [groupLoadNote(out).text] : [];
    },
    async applyView(view) {
        const {applyRecordedView} = await import("./sceneViewRestore");
        return missingNote(await applyRecordedView(view));
    },
    restoreClash: restoreClashCheck,
    openDetail: openDetailModel,
    applyCamera(camera) {
        void import("./loadingView").then(({applyLoadingView}) => applyLoadingView(camera));
    },
};

/** Put `state` back in the scene through the live viewer. */
export function applySceneStateLive(scope: string, state: SceneRecord, opts?: ApplyOptions): Promise<ApplyOutcome> {
    return applySceneState(liveSceneStateDeps, scope, state, opts);
}

/** What the scene shows now, as a state, and how many loaded models cannot be loaded again from a
 *  record (a file opened from disk, a model built in this page) and are left out of it. */
export async function captureSceneState(): Promise<{state: SceneRecord; leftOut: number}> {
    const [{describeLoadedModel}, {captureView}, {captureLoadingView}, {isDetailModelKey}] = await Promise.all([
        import("./sceneRestore"),
        import("./sceneViewRestore"),
        import("./loadingView"),
        import("@/state/detailModelsStore"),
    ]);
    const loaded = useModelState.getState().loadedSourceNames;
    const clash = useClashCheckStore.getState();
    const models: RecordedModel[] = [];
    // Files the kept members load whole: in the state through them.
    const byMembers = new Set(
        (lastMembers?.members ?? []).flatMap((m) => (m.target.kind === "file" ? [m.target.source_key] : [])),
    );
    let leftOut = 0;
    for (const name of loaded) {
        const m = describeLoadedModel(name);
        if (m) models.push(m);
        else if (name === clash.detailGlbKey || isDetailModelKey(name)) continue;
        else if (!byMembers.has(name) || isLocalSource(name)) leftOut += 1;
    }
    const target = clash.assetTarget ?? (clash.sourceKey ? {kind: "file" as const, sourceKey: clash.sourceKey} : null);
    const key = clash.runKey ? {runKey: clash.runKey} : clash.derivedKey ? {resultKey: clash.derivedKey} : null;
    const view = captureView();
    const camera = captureLoadingView();
    const state: SceneRecord = {
        v: 1,
        models,
        camera,
        ...(view ? {view} : {}),
        ...(lastMembers && loaded.size ? {members: [...lastMembers.members]} : {}),
        ...(target && key && clash.result ? {clash: {target, ...key}} : {}),
        ...(clash.detailGlbKey && clash.detailDerivedKey && loaded.has(clash.detailGlbKey)
            ? {detail: {glbKey: clash.detailGlbKey, statsKey: clash.detailDerivedKey, ...(clash.detailSpec ? {spec: clash.detailSpec} : {})}}
            : {}),
    };
    return {state, leftOut};
}
