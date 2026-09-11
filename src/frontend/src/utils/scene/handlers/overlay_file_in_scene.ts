// Add a stored GLB to the current scene without replacing what's
// already there. Bypasses the VIEW_FILE_OBJECT flatbuffer roundtrip
// (which the server hard-codes to SceneOperations.REPLACE) by pulling
// the blob directly through the REST API and feeding it to
// loadModel with placement "overlay", so the new model lands in the
// shared recentered frame and overlays the previous one in place.
//
// Selection-mesh implications: each loaded model gets its own
// CustomBatchedMesh + per-model selection overlay, so picking on one
// doesn't bleed into the other. Tree view (single-model state) keeps
// showing only the first model's hierarchy — the second model is
// visible but won't have tree entries. That's acceptable for the
// debug/diff use case (overlay STEP gold + XML output to see
// missing/displaced plates).

import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {loadModel} from "@/components/viewer/sceneHelpers/loadModel";
import {ensureConvertedGlb} from "@/services/conversion";
import {runtime} from "@/runtime/config";
import {capabilities} from "@/services/capabilities";

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
    if (!capabilities.files.supports("blobUrl")) {
        // The overlay streams a stored GLB by key, which only a transport
        // that serves blob URLs can do — desktop mode opens external apps,
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
    // Stream the GLB straight from storage into GLTFLoader rather than
    // buffering the whole file in memory first. The old path was getBlob
    // (full ArrayBuffer) → new Blob([...]) (a second copy) → object URL →
    // GLTFLoader reads it back into a third ArrayBuffer: ~3x the file size
    // held transiently, plus a giant contiguous allocation that can
    // fragment/fail on big models. Streaming peaks at ~1x file + parsed
    // buffers. `loadModel` does the presigned-direct-then-authed-/blobs
    // dance, the admin load metrics and the source registration; this is the
    // StorageBrowser load path, so it's where most loads happen.
    //
    // placement "overlay" keeps what is already in the scene, and carries
    // translate=true with it: the overlay reuses the first-loaded model's
    // cached modelStore.translation so it lands in the same recentered frame
    // (a replace's translate=false would re-derive from this model's bbox and
    // offset it). If nothing is cached yet (overlay is the first load) the
    // loader computes one as usual — same as a normal first load.
    await loadModel({
        sourceName,
        bytes: {from: "storage", scope, glbKey},
        placement: "overlay",
        presignFallbackWarning:
            "overlay: presigned GLB load failed, falling back to authed streaming GET",
    });
}
