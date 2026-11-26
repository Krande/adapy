// The one load path every entry point goes through.
//
// A REST view, a storage overlay, an in-memory compile result and a websocket
// REPLACE all did the same four things — get the bytes, hand them to the
// loader, register the source on the model session, record the load metrics —
// and each kept its own copy of them. The presigned-then-authed-GET fallback
// alone was written out twice, verbatim, in two files that must not drift:
// miss a `metrics?.setTransport` and a load loses its provenance, miss the
// Authorization header and it 403s, and neither mistake shows up until an
// admin opens the metrics table or a presign starts failing.
//
// What stays with each entry point is what is genuinely different about it:
// which REST call finds the blob, how a websocket frame is decoded, whether
// the model replaces the scene or overlays it.

import * as THREE from "three";

import {capabilities} from "@/services/capabilities";
import type {PresignedDownload} from "@/services/capabilities/types";
import {useModelState} from "@/state/modelState";
import {duringSceneReplace} from "@/state/sceneReplace";
import {directStoreReachable} from "@/utils/scene/directStoreProbe";
import {beginLoadMetrics} from "@/utils/scene/loadMetrics";
import {disposeObject3D} from "@/utils/scene/dispose_object";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useStatsStore} from "@/state/statsStore";
import type {SourceUpAxis} from "@/utils/scene/sourceUpAxis";
import {isNativeViewportActive, showInNativeViewport} from "@/viewport/native/nativeModel";
import {refreshModelPoses} from "@/viewport/api/modelPose";
import {setupModelLoaderAsync, type SetupModelPrepareHook} from "./setupModelLoader";
import {recordModelLoad} from "./loadedModelJournal";

/** Where a model's bytes come from. */
export type ModelBytes =
    /** A stored blob, read straight from the object store. Presigned-direct
     *  when the backend can vend one, else the authed streaming `/blobs` GET. */
    | {from: "storage"; scope: string; glbKey: string}
    /** An already-usable URL: a `blob:` from a websocket frame, or any URL the
     *  caller has resolved itself. Never revoked here — the caller owns it.
     *  `headers` go with the request on either viewport (a relayed blob's
     *  authorization; a presigned or `blob:` URL needs none). */
    | {from: "url"; url: string; headers?: Record<string, string>}
    /** GLB bytes already in memory (an in-browser compile). Wrapped in a blob
     *  URL for the duration of the load and revoked after. */
    | {from: "bytes"; bytes: Uint8Array};

export interface LoadModelSource {
    /** The durable storage key / filename the viewer shows as loaded. */
    sourceName: string;
    /** What the model is called to a person: its root row in the Scene tree. Absent: the model's
     *  own root name, else the source name. */
    displayName?: string;
    bytes: ModelBytes;
    /**
     * "replace" tears the scene down first (the single-model view); "overlay"
     * adds to whatever is already there, keeping the earlier models' groups;
     * "replaceSource" also keeps everything else, but first unloads any earlier
     * load of this same `sourceName` (a compile/recompile replacing only its
     * own result). On an empty scene it falls back to "replace" exactly, so a
     * first load still derives a fresh translation and frames as today.
     */
    placement?: "replace" | "overlay" | "replaceSource";
    /** Reuse the cached scene translation rather than deriving a new one.
     *  Defaults per placement: overlays and "replaceSource" land in the
     *  existing recentered frame (true), a replace derives its own (false). */
    translate?: boolean;
    prepareHook?: SetupModelPrepareHook;
    /** Override the post-load auto-fit (undefined = scene config; false = never). */
    autoFitOverride?: boolean;
    sourceUpAxis?: SourceUpAxis;
    /**
     * The model's nodes are to be placed from outside (viewport/api/modelPose.ts) instead of by an
     * animation of its own. The three.js-free viewport keeps every node apart for it; the three.js
     * viewport needs nothing told, a node there is an object of the scene either way.
     */
    posable?: boolean;
    /** Record admin load metrics for this load. Defaults on for stored blobs,
     *  off for bytes the viewer already has. */
    metrics?: boolean;
    /**
     * What to log when a presigned load fails and the authed GET takes over.
     * Passed in rather than composed here so each entry point keeps its own
     * wording in its own file.
     */
    presignFallbackWarning?: string;
}

/** Unload this source's own earlier load, if one is on screen, leaving every other loaded source
 *  (another model, a streamed FEA result, the other LOD's result) untouched. Used by
 *  "replaceSource" instead of `unload_any_source`, whose streaming-FEA branch would `clearScene`. */
async function unloadSameSource(sourceName: string | undefined): Promise<void> {
    if (!sourceName || !useModelState.getState().loadedSourceNames.has(sourceName)) return;
    const {unload_source_from_scene} = await import("@/utils/scene/handlers/unload_source_from_scene");
    unload_source_from_scene(sourceName);
}

/** Run the actual loader for one resolved URL.
 *
 * `treatAsReplace` is a snapshot, taken once by the caller before any of this source's bytes were
 * fetched, of whether the scene had nothing loaded at all. A "replaceSource" load with nothing to
 * keep behaves exactly as "replace" (a fresh translation, a fresh frame); a "replaceSource" load
 * that finds something already there only unloads its own earlier result and overlays the rest.
 *
 * `ticket`, when given, is checked before this load touches the scene at all (clears it, unloads
 * its own earlier source, or hands bytes to the three.js-free viewport): a "replace"/"replaceSource"
 * load whose window (loadModel, below) has already timed out must not clear or replace anything
 * once it finally gets this far. */
async function loadFrom(
    source: LoadModelSource,
    url: string,
    requestHeaders: Record<string, string> | undefined,
    metrics: ReturnType<typeof beginLoadMetrics>,
    treatAsReplace: boolean,
    ticket?: ReplaceTicket,
): Promise<THREE.Group | undefined> {
    const {sourceName, placement = "replace"} = source;
    if (ticket?.abandoned) return undefined;
    const isReplaceSource = placement === "replaceSource" && !treatAsReplace;
    // The three.js-free viewport is on screen: it takes the GLB as bytes, and no three.js scene is built.
    if (isNativeViewportActive()) {
        const nativePlacement: "replace" | "overlay" = treatAsReplace ? "replace" : "overlay";
        // `isAbandoned` is checked again right after the fetch, inside `showInNativeViewport`,
        // before it swaps the model in: a replace whose own window (loadModel, below) timed out
        // while this fetch was still in flight must not swap a stale model in once it finally
        // resolves, on a viewport that has no scene graph to remove it from afterwards.
        const show = () =>
            showInNativeViewport(
                url,
                requestHeaders,
                sourceName,
                nativePlacement,
                source.displayName,
                source.posable,
                source.autoFitOverride === false,
                source.translate,
                () => ticket?.abandoned === true,
            );
        if (treatAsReplace) {
            // The scene's one teardown (clear_scene), as a replace takes it on the three.js viewport too.
            const [{duringSceneReplace}, {clearScene}] = await Promise.all([
                import("@/state/sceneReplace"),
                import("@/utils/scene/handlers/clear_scene"),
            ]);
            await duringSceneReplace(async () => {
                // The viewport swaps the model in place and reads the loaded sources to do it (see
                // `keepLoadedSources`): they are its to end, not the teardown's.
                await clearScene({reason: "replace", replacing: true, keepQueuedLoads: true, keepProcedural: true, keepLoadedSources: true});
                try {
                    await show();
                } catch (e) {
                    // The old model is already off the screen: the loaded sources must not go on
                    // naming it (its checkbox ticked, its eye toggling nothing). The teardown it was
                    // kept from, now.
                    await clearScene({reason: "replace failed", replacing: true, keepQueuedLoads: true, keepProcedural: true});
                    throw e;
                }
            });
        } else if (isReplaceSource) {
            // Only this source's own earlier load goes; a streamed FEA result drawn on another
            // model, another model, and the other LOD's result all stay exactly as they are.
            await unloadSameSource(sourceName);
            await show();
        } else {
            // A result that was shown ends with the model it was drawn on, as on a replace.
            const {clearActiveFeaStreaming} = await import("@/utils/scene/fea/streaming/teardown");
            clearActiveFeaStreaming();
            await show();
        }
        return undefined;
    }
    if (placement === "overlay" || isReplaceSource) {
        if (isReplaceSource) await unloadSameSource(sourceName);
        return await setupModelLoaderAsync({
            modelUrl: url,
            translate: source.translate ?? true,
            prepareHook: source.prepareHook,
            sourceName,
            displayName: source.displayName,
            requestHeaders,
            metrics,
            autoFitOverride: source.autoFitOverride,
            sourceUpAxis: source.sourceUpAxis,
        });
    }
    // "replace", or "replaceSource" with nothing else loaded (treatAsReplace): behaves exactly
    // like a replace, framed fresh.
    // Dynamic for the same reason the entry points imported it dynamically:
    // replace_model drags in the scene-teardown stack, and the boot path
    // must not pay for it.
    const {replace_model} = await import("@/utils/scene/handlers/update_scene_from_message");
    return await replace_model({
        url,
        prepareHook: source.prepareHook,
        sourceName,
        displayName: source.displayName,
        translate: source.translate ?? false,
        requestHeaders,
        metrics,
        autoFitOverride: source.autoFitOverride,
    });
}

/** Stream a stored blob in, presigned-direct first. The fallback covers the
 *  LOAD as well as the presign: local backends 503 the presign, and an object
 *  stored without the Content-Encoding metadata the browser needs decodes to
 *  garbage on the direct URL but streams correctly through the server, which
 *  forwards `Content-Encoding: gzip` reliably. An object store this browser
 *  cannot reach at all doesn't fail the load, it hangs it, so the presigned
 *  path is only taken once `directStoreReachable` has seen the store answer. */
async function loadStoredBlob(
    source: LoadModelSource & {bytes: {from: "storage"; scope: string; glbKey: string}},
    metrics: ReturnType<typeof beginLoadMetrics>,
    treatAsReplace: boolean,
    ticket?: ReplaceTicket,
): Promise<THREE.Group | undefined> {
    const {scope, glbKey} = source.bytes;
    const {files} = capabilities;
    const fallBack = (reason: string, detail?: unknown) => {
        if (source.presignFallbackWarning) console.warn(source.presignFallbackWarning, reason, detail ?? "");
        metrics?.setFallbackReason(reason);
    };

    let presigned: PresignedDownload | null = null;
    try {
        presigned = await files.requestDownloadUrl(scope, glbKey);
    } catch (e) {
        fallBack("presign_failed", e);
    }
    if (presigned && !(await directStoreReachable(presigned.url))) {
        fallBack("store_unreachable");
        presigned = null;
    }
    // The presign (and its reachability probe) is the async gap this ticket is most likely to
    // cross: re-check right before handing the resolved URL to loadFrom, which clears/replaces
    // the scene.
    if (ticket?.abandoned) return undefined;
    if (presigned) {
        try {
            metrics?.setTransport("presigned");
            metrics?.setUrl(presigned.url);
            return await loadFrom(source, presigned.url, undefined, metrics, treatAsReplace, ticket);
        } catch (e) {
            fallBack("direct_load_failed", e);
        }
    }
    if (ticket?.abandoned) return undefined;
    const {getAccessToken} = await import("@/services/auth/oidc");
    const url = files.blobUrl(scope, glbKey);
    const token = getAccessToken();
    metrics?.setTransport("relayed");
    metrics?.setUrl(url);
    return await loadFrom(
        source,
        url,
        token ? {Authorization: `Bearer ${token}`} : undefined,
        metrics,
        treatAsReplace,
        ticket,
    );
}

// A "replace" / "replaceSource" load snapshots `treatAsReplace` (loadAndRegister, below) before it
// fetches a single byte, and registers only after the whole load settles -- a wide window. Two
// such loads started together on an EMPTY scene (e.g. superimpose) would each find the registry
// empty and both take the full "replace" path; `setLoadedSourceName` (loadAndRegister) "opens a
// fresh session", and whichever finished last would simply erase the other's name, though its
// model is still in the scene. Serializing them -- the second starts only once the first has
// registered -- fixes that, since by then the registry is no longer empty for it to mistake for a
// first load.
let replaceQueue: Promise<unknown> = Promise.resolve();

// A predecessor that never settles (an object store this browser cannot reach, a stalled stream --
// see loadStoredBlob's own comment) must not freeze every later replace for the rest of the
// session: before this cap, `replaceQueue` chained onto that hung promise forever. Each load waits
// for its predecessor for at most QUEUE_WAIT_MS, then proceeds anyway (the predecessor's eventual
// settlement, if it ever comes, is no longer anyone's to wait for). A well-behaved predecessor that
// settles well inside the cap changes nothing: `Promise.race` still picks it, not the delay.
const QUEUE_WAIT_MS = 30_000;

// The window a single replace/replaceSource load is itself allowed, below: without this, a hung
// fetch kept `sceneReplacing()` true (state/sceneReplace.ts) for the rest of the session, because
// QUEUE_WAIT_MS only bounds how long the NEXT load waits for this one, not how long this one may
// itself run. After that cap tripped, a second load went on alongside the still-running first one,
// and a LATE settle of the first still ran `setLoadedSourceName`/`registerLoadedSource` -- opening
// a fresh session and registering a stale group over the newer load's. `withLoadWindow` (below)
// ends this load's own window on a timeout too, and the `ticket.abandoned` checks threaded through
// loadFrom/loadStoredBlob/loadAndRegister make that late settlement register nothing.
const LOAD_WINDOW_MS = 30_000;

/** Resolves after `ms`, handing back the timer so a winning race can cancel it -- otherwise every
 *  call leaves a pending timer behind even once its predecessor settles promptly. */
function capTimer(ms: number): {promise: Promise<void>; timer: ReturnType<typeof setTimeout>} {
    let timer!: ReturnType<typeof setTimeout>;
    const promise = new Promise<void>((resolve) => {
        timer = setTimeout(resolve, ms);
    });
    return {promise, timer};
}

/** One replace/replaceSource load's claim on the scene. `abandoned` is set once this load's own
 *  window (LOAD_WINDOW_MS) has timed out: the load may still be running underneath (a fetch has no
 *  cancel here), but it must no longer clear/replace the scene or touch the model-session registry
 *  once it does settle. `gen` identifies the ticket for logging only; nothing branches on it. */
interface ReplaceTicket {
    gen: number;
    abandoned: boolean;
}

let replaceGen = 0;

class ReplaceTimedOut extends Error {
    constructor(ms: number) {
        super(`a replace/replaceSource load did not finish within ${ms}ms; ending its window`);
        this.name = "ReplaceTimedOut";
    }
}

/** Race `load` against its own LOAD_WINDOW_MS. On a timeout, mark `ticket` abandoned and reject
 *  with ReplaceTimedOut instead of waiting for `load` -- the window (duringSceneReplace, the
 *  caller) then ends on success, failure, OR timeout, whichever comes first. `load` is not
 *  cancelled: it keeps running, and the `ticket.abandoned` checks in loadFrom/loadStoredBlob/
 *  loadAndRegister are what make its eventual, late settlement a no-op. */
function withLoadWindow<T>(load: Promise<T>, ticket: ReplaceTicket): Promise<T> {
    const {promise: capped, timer} = capTimer(LOAD_WINDOW_MS);
    const timedOut = capped.then((): never => {
        ticket.abandoned = true;
        throw new ReplaceTimedOut(LOAD_WINDOW_MS);
    });
    return Promise.race([load, timedOut]).finally(() => clearTimeout(timer));
}

/**
 * Load one model into the scene and register it on the model session.
 *
 * Returns the group it added, or undefined when the loader produced none (a replace with no
 * scene yet, or a load whose own window timed out and was abandoned -- see LOAD_WINDOW_MS). Load
 * metrics are recorded and, on a throw, failed before the error is re-raised to the caller's own
 * handler.
 *
 * A replace runs, registration included, inside `duringSceneReplace` (state/sceneReplace). The
 * teardown empties the loaded sources and the new one is registered only after the loader returns
 * (below): ending the replace window before that registration left the scene "empty" at the moment
 * the replace ended, and the rule that sends the camera home on an emptied scene
 * (cameraHomeOnEmpty) reset the user's view on every compile that showed its result.
 */
export async function loadModel(source: LoadModelSource): Promise<THREE.Group | undefined> {
    const placement = source.placement ?? "replace";
    if (placement !== "replace" && placement !== "replaceSource") return await loadAndRegister(source);
    // "replaceSource" runs inside the same window as "replace": on a scene with nothing else
    // loaded it unloads down to empty too (see treatAsReplace below), and cameraHomeOnEmpty must
    // not take that moment for the user clearing the scene.
    //
    // Chained onto the queue (above) rather than run directly: an earlier replace/replaceSource
    // still in flight must finish -- registration included -- before this one's own
    // `treatAsReplace` snapshot is taken, whatever its own result. Capped (above): a predecessor
    // that never settles is waited for at most QUEUE_WAIT_MS, then this load proceeds anyway.
    const prev = replaceQueue.catch(() => {});
    let settled = false;
    const waited = prev.then(() => {
        settled = true;
    });
    const {promise: capped, timer} = capTimer(QUEUE_WAIT_MS);
    const ticket: ReplaceTicket = {gen: ++replaceGen, abandoned: false};
    const run: Promise<THREE.Group | undefined> = Promise.race([waited, capped])
        .finally(() => clearTimeout(timer))
        .then(() => {
            if (!settled) {
                console.warn(
                    `[loadModel] a queued replace/replaceSource load has not settled after ${QUEUE_WAIT_MS}ms; proceeding anyway`,
                    source.sourceName ?? "(unnamed)",
                );
            }
        })
        .then(() => duringSceneReplace(() => withLoadWindow(loadAndRegister(source, ticket), ticket)));
    // What the NEXT load waits on: swallowed here so this load's own timeout (or any other
    // rejection) cannot itself become something a later load has to wait out past QUEUE_WAIT_MS.
    // The ORIGINAL caller still awaits `run` itself, unswallowed, below.
    replaceQueue = run.catch(() => {});
    return await run;
}

async function loadAndRegister(source: LoadModelSource, ticket?: ReplaceTicket): Promise<THREE.Group | undefined> {
    const {sourceName, bytes, placement = "replace"} = source;
    // Snapshotted before any of this load's bytes are fetched: a "replaceSource" load that finds
    // the scene already empty behaves exactly like "replace" (fresh translation, fresh frame)
    // rather than unloading-then-overlaying onto nothing.
    const treatAsReplace = placement === "replace" || (placement === "replaceSource" && useModelState.getState().loadedSourceNames.size === 0);
    const stored = bytes.from === "storage";
    const metrics = (source.metrics ?? stored)
        ? beginLoadMetrics({
              scope: stored ? bytes.scope : scopeUrlPart(useScopeStore.getState().current),
              key: stored ? bytes.glbKey : sourceName,
              sourceName,
              transport: "unknown",
          })
        : null;

    // A load whose window has already timed out (checked again below, after the loader returns,
    // and inside loadFrom/loadStoredBlob at each async gap) registers nothing and leaves no trace
    // but a failed metrics entry -- the scene it would have touched is whatever the NEXT load left.
    const abandon = (): undefined => {
        metrics?.fail("superseded");
        return undefined;
    };

    let group: THREE.Group | undefined;
    let objectUrl: string | null = null;
    try {
        if (bytes.from === "storage") {
            group = await loadStoredBlob({...source, bytes}, metrics, treatAsReplace, ticket);
        } else if (bytes.from === "bytes") {
            if (ticket?.abandoned) return abandon();
            objectUrl = URL.createObjectURL(
                new Blob([bytes.bytes], {type: "model/gltf-binary"}),
            );
            group = await loadFrom(source, objectUrl, undefined, metrics, treatAsReplace, ticket);
        } else {
            if (ticket?.abandoned) return abandon();
            group = await loadFrom(source, bytes.url, bytes.headers, metrics, treatAsReplace, ticket);
        }
    } catch (e) {
        metrics?.fail(
            e instanceof Error ? e.message : String(e),
            e instanceof Error ? e.stack : undefined,
        );
        throw e;
    } finally {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
    }
    // The loader itself (a GLTF parse, a slow native-viewport show) is where a load is most likely
    // to cross into its own timeout: re-checked once more, after it returns, before this group
    // reaches the scene's registry at all. `setupModelLoaderAsync`/`replace_model` already added
    // the group to `main_scene` by the time this runs (see loadFrom): removing it first, before
    // disposing its GPU resources, is what actually keeps a late load off screen -- `disposeObject3D`
    // frees textures/geometries but never touches the scene graph, so without this the group stayed
    // a (disposed, re-uploaded-on-next-render) sibling of whatever the next load put there.
    if (ticket?.abandoned) {
        group?.removeFromParent();
        disposeObject3D(group);
        return abandon();
    }
    // Remembered so that a switch to the other viewport can load the same models there.
    recordModelLoad(source);

    // Register the source on the model session. Order matters on a replace:
    // setLoadedSourceName opens a fresh session (dropping any prior overlay
    // set's group refs), so it must run BEFORE this model's group is
    // registered — otherwise the group we just registered is cleared, leaving
    // the model in loadedSourceNames with no group and thus a non-toggleable
    // eye in the loaded-models list (couldn't hide the original under an
    // overlay).
    //
    // An unnamed source (a websocket frame that carried no filename) registers
    // nothing: there would be no key to unload or toggle it by.
    const ms = useModelState.getState();
    if (treatAsReplace && sourceName) ms.setLoadedSourceName(sourceName);
    if (group && sourceName) ms.registerLoadedSource(sourceName, group);
    // The three.js-free viewport took the model and built no group: the source is loaded all the same.
    else if (!group && sourceName && isNativeViewportActive()) ms.registerLoadedSourceName(sourceName);
    // Whoever places this model's nodes places them in the model that is here now.
    refreshModelPoses();
    // The take-off the conversion wrote beside this model's GLB, for the model that is now the
    // ACTIVE one -- which is the question the Stats and Take-off panels answer. Keyed on that
    // rather than on `placement`: the storage browser loads every file as an "overlay" (it keeps
    // what is already in the scene), so a replace-only rule fetched a take-off for almost
    // nothing. Fire-and-forget: statistics never hold up a load, and a model with no sidecar
    // (an FEA result, a big source the conversion did not take off) simply has none.
    if (stored && sourceName && useModelState.getState().loadedSourceName === sourceName) {
        void useStatsStore
            .getState()
            .fetchModelStats(bytes.scope, "", bytes.glbKey)
            .catch(() => undefined);
    }
    return group;
}
