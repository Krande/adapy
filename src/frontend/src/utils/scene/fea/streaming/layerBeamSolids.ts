// FEA streaming: beam sections as plain data.
//
// Owns: the optional beam-solid mesh of a result as typed arrays, for a viewport that draws from
// them (a result layer, viewport/api/resultLayer.ts): each beam element's cross-section swept
// along its axis, with the part of the index list each beam owns and the mapping that lets it
// follow the nodes (per vertex: its beam's two end nodes and how far between them it sits).
// Read from either artefact the bake can write, as the three.js loader does (./beamSolids.ts):
//
//  * the compact one (`beam_solids_compact_url`), expanded into vertices, indices, ranges and
//    the node mapping by the compute capability;
//  * the mesh GLB (`beam_solids_url`) with its element ranges and node mapping sidecars.
//
// Best-effort: a missing or corrupt artefact logs, says why through the store (the toggle shows
// it) and gives null. No three.js in this module or in anything it imports.

import {capabilities} from "@/services/capabilities";
import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {fetchBeamSolidsWarp, type ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {fetchMeshElements} from "@/services/feaMeshElements";
import type {FeaManifest} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {usePerfStore} from "@/state/perfStore";
import {readGlbMesh} from "@/utils/glb/glbMesh";
import {BEAM_SOLIDS_PERF_OPT_OUT, describeBeamSolidsFailure} from "./beamSolidsToggle";

export interface BeamSolidData {
    /** xyz per vertex, in the result mesh's own axes. */
    positions: Float32Array;
    indices: Uint32Array;
    /** ``E<label>`` -> ``[start, count]`` in ``indices``, for each beam that has triangles. */
    drawRanges: Map<string, [number, number]>;
    /** The same as parallel lists, ascending by start: each beam's label, and its start and
     *  count in ``indices`` (two numbers per beam). */
    labels: Uint32Array;
    segments: Uint32Array;
    /** Per vertex, its beam's end nodes and its place between them; null when the bake wrote
     *  none, and the solids then stay where they are while the nodes move. */
    warp: ParsedBeamSolidsWarp | null;
}

/** The beam solids of `manifest`, or null when it has none or they could not be read (the reason
 *  is then in the store). `mainPositions` are the result mesh's node positions. */
export async function loadBeamSolidData(
    fetcher: FeaFetcher,
    manifest: FeaManifest,
    mainPositions: Float32Array,
    compute: Pick<typeof capabilities.compute, "expandBeamSolids"> = capabilities.compute,
): Promise<BeamSolidData | null> {
    const compactUrl = manifest.mesh.beam_solids_compact_url;
    const glbUrl = manifest.mesh.beam_solids_url;
    const setUnavailable = useFeaAnimationStore.getState().setBeamSolidsUnavailable;
    setUnavailable(null);
    if (!compactUrl && !glbUrl) return null;
    if (usePerfStore.getState().hideBeamSolids) {
        setUnavailable(BEAM_SOLIDS_PERF_OPT_OUT);
        return null;
    }
    try {
        let positions: Float32Array, indices: Uint32Array;
        let label: ArrayLike<number>, triStart: ArrayLike<number>, triCount: ArrayLike<number>;
        let warp: ParsedBeamSolidsWarp | null = null;
        if (compactUrl) {
            const expanded = await compute.expandBeamSolids(await fetcher(compactUrl), mainPositions);
            ({positions, indices} = expanded);
            label = expanded.elemLabel;
            triStart = expanded.elemTriStart;
            triCount = expanded.elemTriCount;
            warp = {n_verts: expanded.nVerts, node0: expanded.node0, node1: expanded.node1, t: expanded.t};
        } else {
            const [bytes, entries] = await Promise.all([
                fetcher(glbUrl as string),
                manifest.mesh.beam_solids_elements_url ? fetchMeshElements(fetcher, manifest.mesh.beam_solids_elements_url) : Promise.resolve([]),
            ]);
            const mesh = readGlbMesh(new Uint8Array(bytes));
            if (mesh.topology !== "triangles" || !mesh.indices) throw new Error("the beam-solid mesh is not a triangle mesh");
            positions = mesh.positions;
            indices = mesh.indices;
            label = entries.map((e) => e.label);
            triStart = entries.map((e) => e.triStart);
            triCount = entries.map((e) => e.triCount);
            if (manifest.mesh.beam_solids_warp_url) {
                try {
                    const read = await fetchBeamSolidsWarp(fetcher, manifest.mesh.beam_solids_warp_url);
                    if (read.n_verts === positions.length / 3) warp = read;
                    else console.warn(`[fea-streaming] beam-solid node mapping has ${read.n_verts} vertices, the mesh ${positions.length / 3}: the solids will not follow the nodes`);
                } catch (err) {
                    console.warn("[fea-streaming] failed to load the beam-solid node mapping:", err);
                }
            }
        }
        if (positions.length === 0 || indices.length === 0) {
            setUnavailable("the artefact holds no beam geometry");
            return null;
        }
        const drawing: number[] = [];
        for (let i = 0; i < label.length; i++) if (triCount[i] > 0) drawing.push(i);
        drawing.sort((a, b) => triStart[a] - triStart[b]);
        const drawRanges = new Map<string, [number, number]>();
        const labels = new Uint32Array(drawing.length);
        const segments = new Uint32Array(drawing.length * 2);
        drawing.forEach((entry, s) => {
            // The ranges count triangles; the index list has three corners for each.
            const start = triStart[entry] * 3, count = triCount[entry] * 3;
            labels[s] = label[entry];
            segments[s * 2] = start;
            segments[s * 2 + 1] = count;
            drawRanges.set(`E${label[entry]}`, [start, count]);
        });
        return {positions, indices, drawRanges, labels, segments, warp};
    } catch (err) {
        console.warn("[fea-streaming] failed to load beam-solid mesh:", err);
        setUnavailable(describeBeamSolidsFailure(err));
        return null;
    }
}
