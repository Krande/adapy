/**
 * Records what is loaded in the scene, per scope, and loads it again after a reload.
 *
 * RECORDING follows the two UI load paths a reload can repeat: the storage browser's load queue
 * (a stored file, by key) and the asset browser's loads (a published node, by node ref). A model
 * leaving the scene leaves the record. The camera is added when the page is hidden.
 *
 * RESTORING loads the recorded models through those same paths, one at a time, skipping any
 * already in the scene. A model that does not load stays in the record and is tried again on the
 * next reload; "Forget restored scene" clears it. Gated by
 * `useSceneRestoreStore.enabled`; with it off, the previous page's record is discarded.
 *
 * REST mode only: elsewhere nothing is recorded.
 */

import {useAssetBrowserStore} from "@/state/assetBrowserStore";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {useSceneRestoreStore} from "@/state/sceneRestoreStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {applyLoadingView, captureLoadingView} from "@/utils/scene/loadingView";

import {
    EMPTY_SCENE_RECORD,
    assetRef,
    readSceneRecord,
    recordedAsset,
    recordedSourceName,
    removedNames,
    restorePlan,
    updateSceneRecord,
    withModel,
    withoutNames,
    writeSceneRecord,
    type RecordedModel,
} from "./sceneRecord";

/** Scene name -> the scope whose record holds it, for models in the scene on this page. */
const recordedScope = new Map<string, string>();
let recording = false;
let restoreStarted = false;
/** Bumped to stop a running restore. */
let restoreGeneration = 0;

const currentScope = () => scopeUrlPart(useScopeStore.getState().current);

function record(m: RecordedModel): void {
    const scope = currentScope();
    recordedScope.set(recordedSourceName(m), scope);
    updateSceneRecord(scope, (r) => ({...r, models: withModel(r.models, m)}));
}

/** Start recording loads and unloads. Idempotent. */
export function startSceneRecording(): void {
    if (recording) return;
    recording = true;

    useModelState.subscribe((s, prev) => {
        if (s.loadedSourceNames === prev.loadedSourceNames) return;
        const byScope = new Map<string, string[]>();
        for (const name of removedNames(prev.loadedSourceNames, s.loadedSourceNames)) {
            const scope = recordedScope.get(name);
            if (scope === undefined) continue;
            recordedScope.delete(name);
            byScope.set(scope, [...(byScope.get(scope) ?? []), name]);
        }
        for (const [scope, names] of byScope) {
            updateSceneRecord(scope, (r) => ({...r, models: withoutNames(r.models, names)}));
        }
    });

    useAssetBrowserStore.subscribe((s, prev) => {
        if (s.loaded === prev.loaded) return;
        const before = new Set(prev.loaded.map((a) => a.sourceName));
        const live = useModelState.getState().loadedSourceNames;
        for (const a of s.loaded) {
            if (!before.has(a.sourceName) && live.has(a.sourceName)) record(recordedAsset(a.ref));
        }
    });

    // Imported lazily: the queue pulls in the streaming loaders.
    void import("@/state/loadQueueStore")
        .then(({useLoadQueueStore}) => {
            useLoadQueueStore.subscribe((s, prev) => {
                const task = prev.current;
                if (!task || s.current === task) return;
                if (s.errors.some((e) => e.name === task.name)) return;
                if (!useModelState.getState().loadedSourceNames.has(task.name)) return;
                record(task.streamer ? {kind: "file", name: task.name, streamer: true} : {kind: "file", name: task.name});
            });
        })
        .catch((e) => console.warn("scene restore: cannot follow the load queue", e));

    window.addEventListener("pagehide", () => {
        try {
            const scope = currentScope();
            const r = readSceneRecord(scope);
            if (!r.models.length) return;
            const camera = captureLoadingView();
            if (camera) writeSceneRecord(scope, {...r, camera});
        } catch {
            // The camera is a convenience.
        }
    });
}

/** Load one stored file through the storage browser's queue; true when it is in the scene. */
async function loadFile(m: Extract<RecordedModel, {kind: "file"}>): Promise<boolean> {
    const {useLoadQueueStore} = await import("@/state/loadQueueStore");
    const settled = () => {
        const q = useLoadQueueStore.getState();
        return q.current?.name !== m.name && !q.queued.some((t) => t.name === m.name);
    };
    await new Promise<void>((resolve) => {
        const unsub = useLoadQueueStore.subscribe(() => {
            if (!settled()) return;
            unsub();
            resolve();
        });
        useLoadQueueStore.getState().enqueue(m.streamer ? {name: m.name, streamer: true} : {name: m.name});
        if (settled()) {
            unsub();
            resolve();
        }
    });
    if (useLoadQueueStore.getState().errors.some((e) => e.name === m.name)) return false;
    return useModelState.getState().loadedSourceNames.has(m.name);
}

/** Load one published node the way the Scene tree's "Load from" does; true when it is in the scene. */
async function loadAsset(scope: string, m: Extract<RecordedModel, {kind: "asset"}>): Promise<boolean> {
    const [{loadAssetNode}, {realDeliveryDeps}] = await Promise.all([
        import("@/utils/groups/groupLoad"),
        import("@/components/asset_browser/AssetsTab"),
    ]);
    const ref = assetRef(m);
    const deps = realDeliveryDeps((name) => useModelState.getState().loadedSourceNames.has(name));
    await loadAssetNode(scope, deps, {
        kind: "node",
        provider: ref.provider,
        collection: ref.collection,
        subject: ref.subject,
        revision: ref.revision,
        node: ref.node ?? null,
    });
    return useModelState.getState().loadedSourceNames.has(recordedSourceName(m));
}

/** Load the current scope's recorded models, once per page. `skip`: scene names something else
 *  is loading. Never throws. */
export async function restoreScene(skip: ReadonlySet<string> = new Set()): Promise<void> {
    if (restoreStarted) return;
    restoreStarted = true;
    const setProgress = useSceneRestoreStore.getState().setProgress;
    try {
        const scope = currentScope();
        const rec = readSceneRecord(scope);
        if (!useSceneRestoreStore.getState().enabled) {
            writeSceneRecord(scope, EMPTY_SCENE_RECORD);
            return;
        }
        const live = useModelState.getState().loadedSourceNames;
        for (const m of rec.models) {
            const name = recordedSourceName(m);
            if (live.has(name) || skip.has(name)) recordedScope.set(name, scope);
        }
        const plan = restorePlan(rec, live, skip);
        if (!plan.length) return;

        const generation = restoreGeneration;
        const progress = {total: plan.length, done: 0, failed: 0, running: true};
        setProgress({...progress});
        let loaded = 0;
        for (const m of plan) {
            if (generation !== restoreGeneration || currentScope() !== scope || !useSceneRestoreStore.getState().enabled) break;
            const name = recordedSourceName(m);
            let ok = useModelState.getState().loadedSourceNames.has(name);
            if (!ok) {
                try {
                    ok = m.kind === "file" ? await loadFile(m) : await loadAsset(scope, m);
                } catch (e) {
                    console.warn("scene restore: load failed for", name, e);
                }
            }
            if (ok) {
                recordedScope.set(name, scope);
                loaded += 1;
                requestRender();
            } else {
                // Kept in the record: a load can fail for a while (a backend still starting) and the
                // next reload should try again. "Forget restored scene" clears it for good.
                console.warn(`scene restore: "${name}" could not be loaded; kept for the next reload`);
                progress.failed += 1;
            }
            progress.done += 1;
            setProgress({...progress});
        }
        if (loaded && rec.camera && generation === restoreGeneration && currentScope() === scope) {
            applyLoadingView(rec.camera);
        }
        setProgress({...progress, running: false});
    } catch (e) {
        console.warn("scene restore: stopped", e);
        const p = useSceneRestoreStore.getState().progress;
        if (p) setProgress({...p, running: false});
    }
}

/** Forget the current scope's record, and stop a restore that is running. What is in the scene
 *  stays; models loaded from now on are recorded again. */
export function forgetRestoredScene(): void {
    restoreGeneration += 1;
    const scope = currentScope();
    writeSceneRecord(scope, EMPTY_SCENE_RECORD);
    for (const [name, s] of recordedScope) if (s === scope) recordedScope.delete(name);
    useSceneRestoreStore.getState().setProgress(null);
}
