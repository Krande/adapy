// FEA streaming: the beam-solid sidecar.
//
// Owns: fetching and parsing the optional beam-solid GLB with its AFEM draw
// ranges into a pickable CustomBatchedMesh (`tryLoadBeamSolids`), and fetching
// the AFBV warp mapping that lets it deform with the nodal field
// (`fetchBeamSolidWarpSidecar`). Both are best-effort: a missing or corrupt
// sidecar logs and returns null, never fails the load. Attaching the mesh to
// the scene and to the session is the caller's job.
//
// Inputs: a blob fetcher, the manifest, the source name (for the pick key),
// and the perf-store opt-outs.

import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";

import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {fetchBeamSolidsWarp, type ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {fetchMeshElements, type MeshElementEntry} from "@/services/feaMeshElements";
import type {FeaManifest} from "@/services/viewerApi";
import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {usePerfStore} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {convert_to_custom_batch_mesh} from "@/utils/scene/convert_to_custom_batch_mesh";
import {findFirstMesh, snapshotBasePositions} from "./sceneMesh";

/** Fetch + parse the beam-solid GLB and its AFEM sidecar, returning a
 *  THREE.Mesh ready to attach to the scene with per-beam drawRanges
 *  already installed. Returns ``null`` if the manifest carries no
 *  beam-solid URL or the fetch failed (logged + non-fatal). */
export async function tryLoadBeamSolids(
    fetcher: FeaFetcher,
    sourceName: string,
    manifest: FeaManifest,
    initialVisible: boolean,
): Promise<{mesh: THREE.Mesh; basePositions: Float32Array} | null> {
    const beamGlbUrl = manifest.mesh.beam_solids_url;
    if (!beamGlbUrl) return null;
    // Perf-store opt-out: when the user wants to A/B against the
    // line-element fallback we skip the GLB fetch + AFEM/AFBV parsing
    // entirely. Toggled live via the Performance panel; takes effect
    // on the next FEA stream load.
    if (usePerfStore.getState().hideBeamSolids) {
        return null;
    }

    try {
        const [buf, afemEntries] = await Promise.all([
            fetcher(beamGlbUrl),
            manifest.mesh.beam_solids_elements_url
                ? fetchMeshElements(fetcher, manifest.mesh.beam_solids_elements_url)
                : Promise.resolve<MeshElementEntry[]>([]),
        ]);
        const blob = new Blob([buf], {type: "model/gltf-binary"});
        const url = URL.createObjectURL(blob);
        let gltfMesh: THREE.Mesh | null = null;
        try {
            const loader = new GLTFLoader();
            const gltf = await new Promise<{scene: THREE.Group}>((resolve, reject) => {
                loader.load(url, resolve as never, undefined, reject);
            });
            gltfMesh = findFirstMesh(gltf.scene);
        } finally {
            URL.revokeObjectURL(url);
        }
        if (!gltfMesh) return null;

        // Build the draw-range Map keyed by ``E${label}`` so the AFEL
        // apply kernel and the click-resolver both find ranges with
        // the same lookup as the main mesh.
        const drawRanges = new Map<string, [number, number]>();
        for (const entry of afemEntries) {
            if (entry.triCount > 0) {
                drawRanges.set(`E${entry.label}`, [
                    entry.triStart * 3,
                    entry.triCount * 3,
                ]);
            }
        }
        // Rename to ``node1`` so the worker-cache filter accepts the
        // companion userData key. The main mesh is ``node0`` —
        // distinct names keep the two meshes' draw-range tables
        // separate in the worker cache.
        gltfMesh.name = "node1";
        gltfMesh.userData.feaBeamSolids = true;
        gltfMesh.userData.feaStreaming = true;
        gltfMesh.visible = initialVisible;

        // Upgrade to a CustomBatchedMesh so clicks resolve through
        // the existing picker pipeline (handleClickMesh → drawRanges
        // → range_id). Without this, raycasts hit a plain Mesh that
        // has no ``unique_key`` and the selection silently no-ops.
        const uniqueKey = `fea-beam-solids::${sourceName}`;
        const custom = convert_to_custom_batch_mesh(
            gltfMesh,
            drawRanges,
            uniqueKey,
            /* is_design */ false,
            /* ada_ext_data */ null,
        );
        // Preserve the userData tags + visibility flags the plain
        // mesh carried; convert_to_custom_batch_mesh copies userData
        // but it's worth being explicit so future tags don't get
        // lost to a helper refactor.
        custom.userData.feaBeamSolids = true;
        custom.userData.feaStreaming = true;
        custom.visible = initialVisible;

        // Register with the off-thread worker cache so the picker's
        // ``queryMeshDrawRange(unique_key, "node1", faceIndex)`` finds
        // the range and ``queryNameFromRangeId(unique_key, rangeId)``
        // returns the element label. Synthetic id_hierarchy with a
        // single FEA-beam root keeps name resolution flat — every
        // beam shows up as ``E${label}`` in the info box.
        const hierarchy: Record<string, [string, string | number]> = {};
        const rangesPlain: Record<string, [number, number]> = {};
        const ROOT_KEY = "fea-beam-solids-root";
        hierarchy[ROOT_KEY] = ["Beam solids", "*"];
        for (const entry of afemEntries) {
            if (entry.triCount > 0) {
                const rid = `E${entry.label}`;
                hierarchy[rid] = [rid, ROOT_KEY];
                rangesPlain[rid] = [entry.triStart * 3, entry.triCount * 3];
            }
        }
        // The Outliner resolves a clicked row through the runtime ``modelKeyMap``:
        // ``model_key`` -> an object whose subtree holds the named mesh.
        // ``setupModelLoader`` registers the main FEA mesh when it loads the GLB;
        // nothing registered this one. So clicking a beam in the tree set the
        // Properties name and made NO 3d selection -- the status bar stayed on
        // "No selection", nothing highlighted, and every selection-driven
        // behaviour was silently skipped for beams.
        const keyMapRef = getViewerRuntime().modelKeyMap;
        if (!keyMapRef.current) keyMapRef.current = new Map();
        keyMapRef.current.set(uniqueKey, custom);

        // Best-effort cache install — if it fails, the mesh still
        // renders, the click just won't resolve.
        void cacheAndBuildTree(uniqueKey, {
            id_hierarchy: hierarchy,
            draw_ranges_node1: rangesPlain,
        });

        // Don't flip vertexColors on here — without a color attribute,
        // three.js renders vertexColors=true geometry as black. The
        // AFEL apply kernel turns vertexColors on at the same time it
        // writes the color attribute, so the first paint lands both
        // together. Until then the GLB's base PBR material colour
        // shows, which is the right "no data" state for solid beams.

        const basePositions = snapshotBasePositions(custom.geometry);
        return {mesh: custom, basePositions};
    } catch (err) {
        // Beam-solid rendering is decorative — log and continue so a
        // missing/corrupt GLB doesn't block rendering of the main mesh.
        // eslint-disable-next-line no-console
        console.warn("[fea-streaming] failed to load beam-solid mesh:", err);
        return null;
    }
}

/** Fetch the AFBV warp sidecar for a loaded beam-solid mesh: per-vertex
 *  (node0, node1, t), required so the solid mesh deforms with the rest of the
 *  structure when warp is applied. Best-effort — returns null without it, and
 *  the solid mesh then stays at base positions under any morph scale (the old
 *  behaviour). Null too when the vertex count does not match `basePositions`. */
export async function fetchBeamSolidWarpSidecar(
    fetcher: FeaFetcher,
    manifest: FeaManifest,
    basePositions: Float32Array,
): Promise<ParsedBeamSolidsWarp | null> {
    // AFBV: per-vertex (node0, node1, t). Required so the
    // solid mesh deforms with the rest of the structure
    // when warp is applied. Best-effort fetch — without it
    // the solid mesh still renders but stays at base
    // positions under any morph scale (the old behaviour).
    if (manifest.mesh.beam_solids_warp_url) {
        try {
            const warp = await fetchBeamSolidsWarp(
                fetcher, manifest.mesh.beam_solids_warp_url,
            );
            if (warp.n_verts === basePositions.length / 3) {
                return warp;
            } else {
                // eslint-disable-next-line no-console
                console.warn(
                    `[fea-streaming] AFBV vertex count ${warp.n_verts} ` +
                    `!= solid mesh vertices ${basePositions.length / 3}; ` +
                    `solid beams won't follow deformation.`,
                );
            }
        } catch (err) {
            // eslint-disable-next-line no-console
            console.warn("[fea-streaming] failed to load AFBV warp sidecar:", err);
        }
    }
    return null;
}
