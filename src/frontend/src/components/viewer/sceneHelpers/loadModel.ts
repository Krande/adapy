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
import {useModelState} from "@/state/modelState";
import {beginLoadMetrics} from "@/utils/scene/loadMetrics";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import type {SourceUpAxis} from "@/utils/scene/sourceUpAxis";
import {setupModelLoaderAsync, type SetupModelPrepareHook} from "./setupModelLoader";

/** Where a model's bytes come from. */
export type ModelBytes =
    /** A stored blob, read straight from the object store. Presigned-direct
     *  when the backend can vend one, else the authed streaming `/blobs` GET. */
    | {from: "storage"; scope: string; glbKey: string}
    /** An already-usable URL: a `blob:` from a websocket frame, or any URL the
     *  caller has resolved itself. Never revoked here — the caller owns it. */
    | {from: "url"; url: string}
    /** GLB bytes already in memory (an in-browser compile). Wrapped in a blob
     *  URL for the duration of the load and revoked after. */
    | {from: "bytes"; bytes: Uint8Array};

export interface LoadModelSource {
    /** The durable storage key / filename the viewer shows as loaded. */
    sourceName: string;
    bytes: ModelBytes;
    /**
     * "replace" tears the scene down first (the single-model view); "overlay"
     * adds to whatever is already there, keeping the earlier models' groups.
     */
    placement?: "replace" | "overlay";
    /** Reuse the cached scene translation rather than deriving a new one.
     *  Defaults per placement: overlays land in the existing recentered frame
     *  (true), a replace derives its own (false). */
    translate?: boolean;
    prepareHook?: SetupModelPrepareHook;
    /** Override the post-load auto-fit (undefined = scene config; false = never). */
    autoFitOverride?: boolean;
    sourceUpAxis?: SourceUpAxis;
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

/** Run the actual loader for one resolved URL. */
async function loadFrom(
    source: LoadModelSource,
    url: string,
    requestHeaders: Record<string, string> | undefined,
    metrics: ReturnType<typeof beginLoadMetrics>,
): Promise<THREE.Group | undefined> {
    const {sourceName, placement = "replace"} = source;
    if (placement === "overlay") {
        return await setupModelLoaderAsync({
            modelUrl: url,
            translate: source.translate ?? true,
            prepareHook: source.prepareHook,
            sourceName,
            requestHeaders,
            metrics,
            autoFitOverride: source.autoFitOverride,
            sourceUpAxis: source.sourceUpAxis,
        });
    }
    // Dynamic for the same reason the entry points imported it dynamically:
    // replace_model drags in the scene-teardown stack, and the boot path
    // must not pay for it.
    const {replace_model} = await import("@/utils/scene/handlers/update_scene_from_message");
    return await replace_model({
        url,
        prepareHook: source.prepareHook,
        sourceName,
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
 *  forwards `Content-Encoding: gzip` reliably. */
async function loadStoredBlob(
    source: LoadModelSource & {bytes: {from: "storage"; scope: string; glbKey: string}},
    metrics: ReturnType<typeof beginLoadMetrics>,
): Promise<THREE.Group | undefined> {
    const {scope, glbKey} = source.bytes;
    const {files} = capabilities;
    try {
        const presigned = await files.requestDownloadUrl(scope, glbKey);
        metrics?.setTransport("presigned");
        metrics?.setUrl(presigned.url);
        return await loadFrom(source, presigned.url, undefined, metrics);
    } catch (e) {
        if (source.presignFallbackWarning) console.warn(source.presignFallbackWarning, e);
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
        );
    }
}

/**
 * Load one model into the scene and register it on the model session.
 *
 * Returns the group it added, or undefined when the loader produced none
 * (a replace with no scene yet). Load metrics are recorded and, on a throw,
 * failed before the error is re-raised to the caller's own handler.
 */
export async function loadModel(source: LoadModelSource): Promise<THREE.Group | undefined> {
    const {sourceName, bytes, placement = "replace"} = source;
    const stored = bytes.from === "storage";
    const metrics = (source.metrics ?? stored)
        ? beginLoadMetrics({
              scope: stored ? bytes.scope : scopeUrlPart(useScopeStore.getState().current),
              key: stored ? bytes.glbKey : sourceName,
              sourceName,
              transport: "unknown",
          })
        : null;

    let group: THREE.Group | undefined;
    let objectUrl: string | null = null;
    try {
        if (bytes.from === "storage") {
            group = await loadStoredBlob({...source, bytes}, metrics);
        } else if (bytes.from === "bytes") {
            objectUrl = URL.createObjectURL(
                new Blob([bytes.bytes], {type: "model/gltf-binary"}),
            );
            group = await loadFrom(source, objectUrl, undefined, metrics);
        } else {
            group = await loadFrom(source, bytes.url, undefined, metrics);
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
    if (placement === "replace" && sourceName) ms.setLoadedSourceName(sourceName);
    if (group && sourceName) ms.registerLoadedSource(sourceName, group);
    return group;
}
