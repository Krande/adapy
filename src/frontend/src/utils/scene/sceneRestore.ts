/**
 * Records what is loaded in the scene, per scope, and loads it again after a reload.
 *
 * RECORDING follows the two UI load paths a reload can repeat: the storage browser's load queue
 * (a stored file, by key) and the asset browser's loads (a published node, by node ref). A model
 * leaving the scene leaves the record. The camera is added when the page is hidden.
 *
 * The hidden objects and the selection are recorded too, by element id per model, a moment after
 * each change and when the page is hidden (utils/scene/sceneViewRestore).
 *
 * RESTORING loads the recorded models through those same paths, one at a time, skipping any
 * already in the scene, then hides and selects again what was hidden and selected. A model that does not load stays in the record and is tried again on the
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
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {beginBulkLoad, captureLoadingView, endBulkLoad} from "@/utils/scene/loadingView";
import {applyRecordedView, captureView} from "@/utils/scene/sceneViewRestore";

/** The bulk-load session's project name: it saves no loading view, so the name is only a label. */
const RESTORE_SESSION = "scene-restore";

import {
    EMPTY_SCENE_RECORD,
    assetRef,
    missingNote,
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
    type SceneRecord,
} from "./sceneRecord";

/** Scene name -> the scope whose record holds it, for models in the scene on this page. */
const recordedScope = new Map<string, string>();
let recording = false;
let restoreStarted = false;
/** Bumped to stop a running restore. */
let restoreGeneration = 0;
/** Whether the hidden objects and the selection are written to the record. Not until the restore
 *  has re-applied the previous page's: the models arriving one by one would otherwise overwrite
 *  that view with the empty one of a half-loaded scene. */
let viewSaving = false;
let viewSaveTimer: ReturnType<typeof setTimeout> | null = null;
const VIEW_SAVE_DELAY_MS = 1500;

/** Write what is hidden and selected now into the current scope's record. */
function saveView(): void {
    if (!viewSaving) return;
    try {
        const scope = currentScope();
        const r = readSceneRecord(scope);
        if (!r.models.length) return;
        writeSceneRecord(scope, {...r, view: captureView()});
    } catch (e) {
        console.warn("scene restore: cannot record the hidden objects and the selection", e);
    }
}

function scheduleViewSave(): void {
    if (!viewSaving) return;
    if (viewSaveTimer) clearTimeout(viewSaveTimer);
    viewSaveTimer = setTimeout(() => {
        viewSaveTimer = null;
        saveView();
    }, VIEW_SAVE_DELAY_MS);
}

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
            if (!before.has(a.sourceName) && live.has(a.sourceName)) record(recordedAsset(a.ref, a.label));
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

    // What is hidden and selected follows every change, a moment after it settles; the page
    // hiding below writes it once more, in case the last change was too recent.
    useVisibilityStore.subscribe(scheduleViewSave);
    useSelectedObjectStore.subscribe((s, prev) => {
        if (s.selectedObjects !== prev.selectedObjects) scheduleViewSave();
    });

    window.addEventListener("pagehide", () => {
        if (viewSaveTimer) {
            clearTimeout(viewSaveTimer);
            viewSaveTimer = null;
        }
        try {
            const scope = currentScope();
            const r = readSceneRecord(scope);
            if (!r.models.length) return;
            const camera = captureLoadingView();
            writeSceneRecord(scope, {...r, ...(camera ? {camera} : {}), ...(viewSaving ? {view: captureView()} : {})});
        } catch {
            // The camera and the view are a convenience.
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
    }, m.label);
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
        if (!plan.length) {
            const note = await restoreView(rec.view, scope);
            if (note) setProgress({total: 0, done: 0, failed: 0, running: false, note});
            return;
        }

        const generation = restoreGeneration;
        const progress = {total: plan.length, done: 0, failed: 0, running: true};
        setProgress({...progress});
        // One bulk load: the camera goes to where it was before the reload, and no model's own
        // fit moves it afterwards. Without a saved camera the first model is framed.
        const session = beginBulkLoad(scope, RESTORE_SESSION, {view: rec.camera ?? null, save: false});
        try {
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
                    requestRender();
                } else {
                    // Kept in the record: a load can fail for a while (a backend still starting) and
                    // the next reload should try again. "Forget restored scene" clears it for good.
                    console.warn(`scene restore: "${name}" could not be loaded; kept for the next reload`);
                    progress.failed += 1;
                }
                progress.done += 1;
                setProgress({...progress});
            }
        } finally {
            endBulkLoad(session);
        }
        // The models are in: hide and select again what was hidden and selected before.
        const note = await restoreView(rec.view, scope);
        setProgress({...progress, running: false, note});
    } catch (e) {
        console.warn("scene restore: stopped", e);
        const p = useSceneRestoreStore.getState().progress;
        if (p) setProgress({...p, running: false});
    } finally {
        // From now on the record follows what is hidden and selected on this page.
        viewSaving = true;
    }
}

/** Re-apply the recorded hidden objects and selection to the models now loaded; returns the one
 *  short note about recorded objects that were not found, or null. */
async function restoreView(view: SceneRecord["view"], scope: string): Promise<string | null> {
    if (!view || currentScope() !== scope) return null;
    try {
        const note = missingNote(await applyRecordedView(view));
        if (note) console.info(`scene restore: ${note}`);
        return note;
    } catch (e) {
        console.warn("scene restore: cannot restore the hidden objects and the selection", e);
        return null;
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
