// Beam solids for the paradoc embed.
//
// The standalone viewer loads a bundle's beam solids through the streaming
// session (tryLoadBeamSolids): a CustomBatchedMesh registered with the picker's
// worker cache, warped by the session's paint path. The embed has none of
// that -- it assembles one GLB per mode and drives a single morph influence --
// so this is the slim version: parse the solid mesh, hang it under the FEA
// primitive, give it a morph that follows the same influence, and colour it
// from the nodes it sits on. With the rotation term in the warp
// (beamSolidDisplacement) a torsion mode turns the sections, which a beam drawn
// as a line cannot show at all.
//
// Only the mesh artefact (`beam_solids_url` + AFBV warp) is read: the compact
// one is expanded by a wasm module the embed does not ship. Docs bundles are
// baked with `beam_solid_format="mesh"` for that reason.

import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";

import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {fetchBeamSolidsWarp, type ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {RESULT_LINE_SEGMENTS_NAME} from "./resultLineSegments";
import {installBeamSolidWarp} from "./streaming/warp";

export const EMBED_BEAM_SOLIDS_NAME = "fea-beam-solids";

export interface EmbedBeamSolids {
    mesh: THREE.Mesh;
    basePositions: Float32Array;
    warp: ParsedBeamSolidsWarp;
}

/** The bundle's beam-solid mesh and its warp map, or null when it has none
 *  (or only the compact artefact, or they do not fit together). Never throws:
 *  beam solids are an alternative rendering, and the model draws without them. */
export async function loadEmbedBeamSolids(
    fetcher: FeaFetcher,
    manifest: FeaManifest,
): Promise<EmbedBeamSolids | null> {
    const glbUrl = manifest.mesh.beam_solids_url;
    const warpUrl = manifest.mesh.beam_solids_warp_url;
    if (!glbUrl || !warpUrl) return null;
    try {
        const [buf, warp] = await Promise.all([fetcher(glbUrl), fetchBeamSolidsWarp(fetcher, warpUrl)]);
        const gltf = await new GLTFLoader().parseAsync(buf, "");
        let mesh: THREE.Mesh | null = null;
        gltf.scene.traverse((o) => {
            if (!mesh && (o as THREE.Mesh).isMesh) mesh = o as THREE.Mesh;
        });
        const found = mesh as THREE.Mesh | null;
        if (!found) return null;
        const position = found.geometry.getAttribute("position");
        if (!position || position.count !== warp.n_verts) {
            // eslint-disable-next-line no-console
            console.warn(
                `[fea-embed] beam solids: ${position?.count ?? 0} vertices but a warp map for ` +
                `${warp.n_verts}; drawing beams as lines`,
            );
            return null;
        }
        return {mesh: found, basePositions: new Float32Array(position.array as ArrayLike<number>), warp};
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[fea-embed] beam solids not loaded:", err);
        return null;
    }
}

/** Per-vertex colour: the two end nodes' colours, interpolated along the beam. */
export function beamSolidColors(warp: ParsedBeamSolidsWarp, nodeColors: THREE.BufferAttribute | THREE.InterleavedBufferAttribute): Float32Array {
    const out = new Float32Array(warp.n_verts * 3);
    for (let v = 0; v < warp.n_verts; v++) {
        const t = warp.t[v];
        const a = warp.node0[v];
        const b = warp.node1[v];
        out[v * 3] = (1 - t) * nodeColors.getX(a) + t * nodeColors.getX(b);
        out[v * 3 + 1] = (1 - t) * nodeColors.getY(a) + t * nodeColors.getY(b);
        out[v * 3 + 2] = (1 - t) * nodeColors.getZ(a) + t * nodeColors.getZ(b);
    }
    return out;
}

/** Hang the beam solids under the FEA primitive, warped by this mode and
 *  coloured like the nodes. The solid shares the primitive's morph influences,
 *  so the oscillation and the scale slider move it with everything else. */
export function attachEmbedBeamSolids(
    primary: THREE.Object3D & {geometry: THREE.BufferGeometry; morphTargetInfluences?: number[]},
    solids: EmbedBeamSolids,
    mode: {field: FeaManifestField; stepValues: Float32Array; nodePositions: Float32Array},
): THREE.Mesh {
    const {mesh, basePositions, warp} = solids;
    const geom = mesh.geometry;
    const nodeColors = primary.geometry.getAttribute("color");
    if (nodeColors) {
        geom.setAttribute("color", new THREE.BufferAttribute(beamSolidColors(warp, nodeColors), 3));
    }
    // Flat shading needs no normals (the bake writes none); it shades each face
    // from its own screen-space derivatives, so the deformed shape lights right.
    mesh.material = new THREE.MeshStandardMaterial({
        vertexColors: !!nodeColors,
        flatShading: true,
        side: THREE.DoubleSide,
        metalness: 0,
        roughness: 0.8,
    });
    installBeamSolidWarp(primary, mesh, basePositions, warp, mode.field, mode.stepValues, mode.nodePositions);
    mesh.name = EMBED_BEAM_SOLIDS_NAME;
    mesh.frustumCulled = false;
    // Attached hidden: setEmbedBeamSolidsVisible flips the solids and the beam
    // lines together, and it only acts on a change -- attached visible, the
    // first "show" would find nothing to do and leave the lines drawn as well.
    mesh.visible = false;
    primary.add(mesh);
    return mesh;
}

/** Show the beams as solids, or as lines. The two are alternatives: with the
 *  solids on, the coloured beam lines step aside. Returns whether it changed
 *  anything, so a caller polling per frame can stay cheap. */
export function setEmbedBeamSolidsVisible(primary: THREE.Object3D, visible: boolean): boolean {
    const solids = primary.getObjectByName(EMBED_BEAM_SOLIDS_NAME);
    if (!solids || solids.visible === visible) return false;
    solids.visible = visible;
    const lines = primary.getObjectByName(RESULT_LINE_SEGMENTS_NAME);
    if (lines) lines.visible = !visible;
    return true;
}
