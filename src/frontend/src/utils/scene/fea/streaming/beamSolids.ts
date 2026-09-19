// FEA streaming: the beam-solid sidecar.
//
// Owns: getting the optional beam-solid mesh with its per-beam draw ranges
// into a pickable CustomBatchedMesh (`tryLoadBeamSolids`), from either of the
// two artefacts the bake can write, and fetching the AFBV warp mapping that
// lets it deform with the nodal field (`fetchBeamSolidWarpSidecar`).
//
// Two artefacts, one mesh:
//
//  * AFBS (`beam_solids_compact_url`) — the per-section outline table plus a
//    56-byte frame per beam, expanded in a worker into the vertex, index,
//    draw-range AND warp buffers. An order of magnitude less to download, and
//    nothing to parse on the main thread.
//  * the GLB (`beam_solids_url`) with its AFEM ranges and AFBV warp sidecar —
//    what every bake wrote before AFBS, and what a bake still writes for
//    `beam_solid_format="mesh"`. Kept verbatim: cached artefacts outlive a
//    viewer release, and a beam the compact format has no room for (tapered,
//    boolean) is only ever in this one.
//
// Past the point where both have produced a mesh and a draw-range table the
// code is shared and unchanged — the picker, the element-field paint and the
// warp cannot tell which artefact they came from.
//
// Both paths are best-effort: a missing or corrupt artefact logs and returns
// null, never fails the load. Attaching the mesh to the scene and to the
// session is the caller's job.
//
// Inputs: a blob fetcher, the manifest, the source name (for the pick key),
// the main mesh's positions (the compact expansion measures the axial warp
// parameter against them), and the perf-store opt-outs.

import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";

import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {
    expandBeamSolidsFromBytes,
    type ExpandedBeamSolids,
} from "@/services/feaBeamSolidsCompact";
import {fetchBeamSolidsWarp, type ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {fetchMeshElements, type MeshElementEntry} from "@/services/feaMeshElements";
import type {FeaManifest} from "@/services/viewerApi";
import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {usePerfStore} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {convert_to_custom_batch_mesh} from "@/utils/scene/convert_to_custom_batch_mesh";
// Inline-bundled worker — Vite handles the import + URL plumbing. Never
// instantiated unless a manifest actually carries a compact artefact.
import BeamSolidsExpandWorker from "./beamSolidsExpand.worker.ts?worker&inline";
import type {BeamSolidsExpandWorkerAPI} from "./beamSolidsExpand.worker";
import {findFirstMesh, snapshotBasePositions} from "./sceneMesh";

export interface LoadedBeamSolids {
    mesh: THREE.Mesh;
    basePositions: Float32Array;
    /** Present only on the compact path, where the warp triple falls out of
     *  the same expansion that built the mesh — no second fetch. */
    warp?: ParsedBeamSolidsWarp;
}

/** Fetch + parse whichever beam-solid artefact the manifest carries and
 *  return a THREE.Mesh ready to attach to the scene with per-beam drawRanges
 *  already installed. Returns ``null`` if the manifest carries no beam-solid
 *  URL or the load failed (logged + non-fatal). */
export async function tryLoadBeamSolids(
    fetcher: FeaFetcher,
    sourceName: string,
    manifest: FeaManifest,
    initialVisible: boolean,
    mainPositions: Float32Array,
): Promise<LoadedBeamSolids | null> {
    const compactUrl = manifest.mesh.beam_solids_compact_url;
    const beamGlbUrl = manifest.mesh.beam_solids_url;
    if (!compactUrl && !beamGlbUrl) return null;
    // Perf-store opt-out: when the user wants to A/B against the
    // line-element fallback we skip the fetch + parsing entirely.
    // Toggled live via the Performance panel; takes effect on the
    // next FEA stream load.
    if (usePerfStore.getState().hideBeamSolids) {
        return null;
    }

    try {
        const built = compactUrl
            ? await buildFromCompact(fetcher, compactUrl, mainPositions)
            : await buildFromGlb(fetcher, beamGlbUrl as string, manifest);
        if (!built) return null;
        const {mesh: beamMesh, entries, warp} = built;

        // Build the draw-range Map keyed by ``E${label}`` so the AFEL
        // apply kernel and the click-resolver both find ranges with
        // the same lookup as the main mesh.
        const drawRanges = new Map<string, [number, number]>();
        for (const entry of entries) {
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
        beamMesh.name = "node1";
        beamMesh.userData.feaBeamSolids = true;
        beamMesh.userData.feaStreaming = true;
        beamMesh.visible = initialVisible;

        // Upgrade to a CustomBatchedMesh so clicks resolve through
        // the existing picker pipeline (handleClickMesh → drawRanges
        // → range_id). Without this, raycasts hit a plain Mesh that
        // has no ``unique_key`` and the selection silently no-ops.
        const uniqueKey = `fea-beam-solids::${sourceName}`;
        const custom = convert_to_custom_batch_mesh(
            beamMesh,
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
        for (const entry of entries) {
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
        // together. Until then the base PBR material colour shows,
        // which is the right "no data" state for solid beams.

        const basePositions = snapshotBasePositions(custom.geometry);
        return {mesh: custom, basePositions, warp};
    } catch (err) {
        // Beam-solid rendering is decorative — log and continue so a
        // missing/corrupt artefact doesn't block rendering of the main mesh.
        // eslint-disable-next-line no-console
        console.warn("[fea-streaming] failed to load beam-solid mesh:", err);
        return null;
    }
}

interface BuiltBeamSolids {
    mesh: THREE.Mesh;
    entries: MeshElementEntry[];
    warp?: ParsedBeamSolidsWarp;
}

/** The compact path: fetch AFBS, expand it (in a worker when we can) and
 *  build the geometry the GLB used to carry — a float32 ``position``
 *  attribute and a uint32 index, and nothing else. No normals: the beam-solid
 *  material is flat-shaded by `convert_to_custom_batch_mesh`, which is why the
 *  GLB never had any either. */
async function buildFromCompact(
    fetcher: FeaFetcher,
    compactUrl: string,
    mainPositions: Float32Array,
): Promise<BuiltBeamSolids | null> {
    const buf = await fetcher(compactUrl);
    const expanded = await expandCompact(buf, mainPositions);
    if (expanded.nVerts === 0) return null;

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute(expanded.positions, 3));
    geom.setIndex(new THREE.Uint32BufferAttribute(expanded.indices, 1));
    const mesh = new THREE.Mesh(
        geom,
        new THREE.MeshStandardMaterial({side: THREE.DoubleSide}),
    );

    const entries: MeshElementEntry[] = new Array(expanded.elemLabel.length);
    for (let i = 0; i < expanded.elemLabel.length; i++) {
        entries[i] = {
            label: expanded.elemLabel[i],
            triStart: expanded.elemTriStart[i],
            triCount: expanded.elemTriCount[i],
        };
    }

    return {
        mesh,
        entries,
        warp: {
            n_verts: expanded.nVerts,
            node0: expanded.node0,
            node1: expanded.node1,
            t: expanded.t,
        },
    };
}

/** Run the expansion in a worker; fall back to the main thread when one
 *  cannot be spawned (a test runner, a hardened embed, an OOM). The fallback
 *  calls the very same function the worker does, so the only difference
 *  between them is which thread pays for it. */
async function expandCompact(
    compact: ArrayBuffer,
    mainPositions: Float32Array,
): Promise<ExpandedBeamSolids> {
    let worker: Worker | null = null;
    try {
        worker = new BeamSolidsExpandWorker();
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[fea-streaming] beam-solid expand worker unavailable:", err);
        return expandBeamSolidsFromBytes(compact, mainPositions);
    }
    try {
        const Comlink = await import("comlink");
        const api = Comlink.wrap<BeamSolidsExpandWorkerAPI>(worker);
        // ``mainPositions`` is a snapshot copy already, but copy again: the
        // session keeps its snapshot for the whole load, and transferring
        // would detach it.
        const positionsCopy = new Float32Array(mainPositions);
        return await api.expand(
            Comlink.transfer(
                {compact, mainPositions: positionsCopy},
                [compact, positionsCopy.buffer as ArrayBuffer],
            ),
        );
    } finally {
        worker.terminate();
    }
}

/** The GLB path, unchanged: the beam-solid GLB plus its AFEM draw ranges. */
async function buildFromGlb(
    fetcher: FeaFetcher,
    beamGlbUrl: string,
    manifest: FeaManifest,
): Promise<BuiltBeamSolids | null> {
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
    return {mesh: gltfMesh, entries: afemEntries};
}

/** Fetch the AFBV warp sidecar for a loaded beam-solid mesh: per-vertex
 *  (node0, node1, t), required so the solid mesh deforms with the rest of the
 *  structure when warp is applied. Only the GLB path needs it — the compact
 *  expansion has already produced the same triple. Best-effort — returns null
 *  without it, and the solid mesh then stays at base positions under any morph
 *  scale (the old behaviour). Null too when the vertex count does not match
 *  `basePositions`. */
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
