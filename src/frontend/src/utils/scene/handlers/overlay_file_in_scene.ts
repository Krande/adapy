// Add a stored GLB to the current scene without replacing what's
// already there. Bypasses the VIEW_FILE_OBJECT flatbuffer roundtrip
// (which the server hard-codes to SceneOperations.REPLACE) by pulling
// the blob directly through the REST API and feeding it to
// setupModelLoaderAsync with translate=false, so the new model lands
// at its real coordinates and overlays the previous one in place.
//
// Selection-mesh implications: each loaded model gets its own
// CustomBatchedMesh + per-model selection overlay, so picking on one
// doesn't bleed into the other. Tree view (single-model state) keeps
// showing only the first model's hierarchy — the second model is
// visible but won't have tree entries. That's acceptable for the
// debug/diff use case (overlay STEP gold + XML output to see
// missing/displaced plates).

import {viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {setupModelLoaderAsync} from "@/components/viewer/sceneHelpers/setupModelLoader";
import {ensureConvertedGlb} from "@/services/conversion";
import {runtime} from "@/runtime/config";
import {beginLoadMetrics} from "@/utils/scene/loadMetrics";

export function derivedKeyForGlb(sourceKey: string): string {
    // Mirrors the server-side derived_key_for(target='glb') convention.
    // GLB-already files have no derivation; everything else is
    // _derived/<sourceKey>.glb. Importantly the SOURCE-IS-GLB case
    // returns sourceKey itself.
    return sourceKey.toLowerCase().endsWith(".glb")
        ? sourceKey
        : `_derived/${sourceKey}.glb`;
}

export async function overlay_file_in_scene(
    sourceName: string,
    explicitDerivedKey?: string,
    opts?: {scope?: string; streamer?: boolean},
): Promise<void> {
    if (!runtime.isRestMode()) {
        // Overlay path is REST-only — desktop mode opens external apps,
        // not a shared 3D scene we can stack into.
        console.warn("overlay_file_in_scene: not in REST mode; ignoring");
        return;
    }

    // ``opts.scope`` overrides the current-scope default for cross-
    // scope overlays — used by the component-preview panel which
    // fetches GLBs from whichever scope the spec was published in
    // (typically a project scope) regardless of which scope the user
    // is currently browsing.
    const scope = opts?.scope ?? scopeUrlPart(useScopeStore.getState().current);

    // Caller (the /convert page's "View in 3D" link) can hand us the
    // exact derived-blob key it just produced. Use it verbatim and
    // skip the ensure-converted dance — the blob is on storage by
    // construction. Without this shortcut the viewer would
    // re-POST /convert which (a) adds latency on the deep-link, and
    // (b) on a race could re-enqueue a conversion that just finished.
    let glbKey: string;
    if (explicitDerivedKey) {
        // The caller may hand us a non-GLB derived product (e.g. an IFC or
        // XML conversion result). The three.js loader only mounts GLB/glTF,
        // so convert that blob to GLB on demand and load the result. The
        // GLB-of-a-derived key lines up with the server's derived_key_for
        // convention (``_derived/<derivedKey>.glb``).
        const isGlbDerived = /\.(glb|gltf)$/i.test(explicitDerivedKey);
        if (isGlbDerived) {
            glbKey = explicitDerivedKey;
        } else {
            if (!runtime.convertEnabled()) {
                console.warn("overlay_file_in_scene: non-GLB derived but conversion disabled");
                return;
            }
            await ensureConvertedGlb(scope, explicitDerivedKey);
            glbKey = derivedKeyForGlb(explicitDerivedKey);
        }
    } else {
        const isGlb = sourceName.toLowerCase().endsWith(".glb");
        if (!isGlb) {
            if (!runtime.convertEnabled()) {
                console.warn("overlay_file_in_scene: non-GLB source but conversion disabled");
                return;
            }
            await ensureConvertedGlb(scope, sourceName, {streamer: opts?.streamer});
        }
        glbKey = derivedKeyForGlb(sourceName);
    }
    // Admin-only opt-in load metrics (null when off / non-admin). This is
    // the StorageBrowser load path, so it's where most loads happen. The
    // GLB is pulled via the same-origin /blobs GET (getBlob) then handed to
    // the loader as an in-memory blob: URL — so point the recorder at the
    // real blobUrl for the network/IO split (Resource Timing has it), and
    // mark download-complete right after getBlob (the loader's blob: parse
    // is then the CPU phase).
    const metrics = beginLoadMetrics({scope, key: glbKey, sourceName, transport: "relayed"});
    metrics?.setUrl(viewerApi.blobUrl(scope, glbKey));

    let group;
    try {
        const blob = await viewerApi.getBlob(scope, glbKey);
        metrics?.markDownloadDone();
        const url = URL.createObjectURL(new Blob([blob], {type: "model/gltf-binary"}));

        // translate=true: pick up the cached modelStore.translation set
        // by the first-loaded model so the overlay lands in the same
        // recentered frame. With translate=false the loader treats this
        // as a "fresh start" and re-derives translation from this
        // model's bbox — which makes the overlay appear offset from
        // whatever was already on screen.
        //
        // If no translation is cached yet (overlay is the first thing
        // loaded), the loader's else branch computes one as usual; same
        // outcome as a normal first load.
        group = await setupModelLoaderAsync(url, true, undefined, sourceName, undefined, metrics);
    } catch (e) {
        metrics?.fail(e instanceof Error ? e.message : String(e));
        throw e;
    }

    // Register the source → group mapping so we can later remove
    // just this overlay without nuking the rest of the scene.
    useModelState.getState().registerLoadedSource(sourceName, group);
}
