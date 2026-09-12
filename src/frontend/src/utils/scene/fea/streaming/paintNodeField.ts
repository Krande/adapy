// FEA streaming: painting a nodal field.
//
// Owns: fetching one step of a nodal field and writing its colours and morph
// delta onto the result mesh, then interpolating the same field onto the
// beam-solid mesh through the AFBV mapping -- colour and warp both -- so a
// displacement field paints and flexes the solid beams in lockstep with the
// shells. Every surface carrying the field gets the same reduction, colormap,
// contour range and morph influence.
//
// Inputs: the session handle, the field + step, the user's view settings, the
// resolved warp source, and the blob fetchers.

import * as THREE from "three";

import type {ContourSettings} from "../contourScale";
import type {FeaFetcher, FeaRangeFetcher} from "@/services/fea/feaFetcher";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import type {FeaManifestField} from "@/services/viewerApi";
import type {FeaSessionHandle} from "@/state/modelSession";
import {applyFieldToMesh} from "../applyField";
import {beamSolidNodalColors} from "../beamSolidNodalColors";
import {expandSourceTriples, sourceVertexIndices} from "../elementLocalGeometry";
import {installBeamSolidWarp} from "./warp";

export async function paintNodeField(args: {
    /** The session handle being painted: the result mesh, its base positions,
     *  and the beam-solid mesh + AFBV mapping when the bake shipped them. */
    active: FeaSessionHandle;
    field: FeaManifestField;
    stepIndex: number;
    /** The component / reduction to colour by. */
    reduction: string;
    /** The morph influence to install: slider position times warp scale. */
    displacementScale: number;
    colormap: string;
    contour: ContourSettings;
    /** The field and step values driving the morph delta, or null to stay
     *  undeformed (see `resolveWarpSource`). */
    warpInfo: {field: FeaManifestField; stepValues: Float32Array} | null;
    rangeFetcher: FeaRangeFetcher;
    fetcher: FeaFetcher;
    cacheKey: string;
}): Promise<void> {
    const {
        active, field, stepIndex, reduction: reductionStr, displacementScale, colormap, contour,
        warpInfo, rangeFetcher, fetcher, cacheKey,
    } = args;
    const colorStepValues = await fetchFieldStep(rangeFetcher, fetcher, field, stepIndex, cacheKey);

    applyFieldToMesh({
        mesh: active.mesh,
        basePositions: active.basePositions,
        colorField: field,
        colorStepValues,
        reduction: reductionStr,
        warpField: warpInfo?.field,
        warpStepValues: warpInfo?.stepValues,
        displacementScale,
        colormap,
        contour,
    });

    // Beam-solid mesh: paint it from the same nodal field.
    //
    // This used to switch vertex colours off, on the reasoning that a
    // beam-solid vertex is not an FEA node. True of the vertex, false of the
    // beam: the AFBV sidecar names each vertex's two end nodes and its axial
    // parameter, which is the very interpolation installBeamSolidWarp uses to
    // MOVE that vertex. Anything that can be interpolated to a position can be
    // interpolated to a colour, so a displacement field now paints the beams as
    // well as the shells — as the reference postprocessor does, and as an element field already did
    // here. Base material on a beam that has a value does not read as "no data";
    // it reads as zero.
    //
    // Warp is independent of colour: install the lerped nodal warp so a
    // displacement field flexes the solid beams in lockstep with the rest of the
    // structure. Without it, scaling the morph influence ×100 leaves rigid solid
    // beams at undeformed positions while the shells fly off.
    if (active.beamSolidMesh) {
        const setVc = (mat: THREE.Material, on: boolean) => {
            if ("vertexColors" in mat && (mat as unknown as {vertexColors: boolean}).vertexColors !== on) {
                (mat as unknown as {vertexColors: boolean}).vertexColors = on;
                mat.needsUpdate = true;
            }
        };
        let painted = false;
        if (active.beamSolidWarp && active.beamSolidBasePositions) {
            const sourceColors = beamSolidNodalColors(
                field,
                colorStepValues,
                reductionStr,
                active.beamSolidWarp,
                colormap,
                active.basePositions.length / 3,
                contour,
            );
            if (sourceColors) {
                const geom = active.beamSolidMesh.geometry;
                // Through the element-local expansion, if one is cached on this
                // geometry from an earlier element field. Same reason the morph
                // goes through it: a buffer sized for the original vertex count
                // does not fit an expanded geometry.
                const nSource = active.beamSolidWarp.n_verts;
                const renderToSource = sourceVertexIndices(geom, nSource);
                const renderColors = expandSourceTriples(sourceColors, renderToSource);
                const existing = geom.getAttribute("color");
                if (existing && existing.count === renderToSource.length && existing.itemSize === 3) {
                    (existing.array as Float32Array).set(renderColors);
                    existing.needsUpdate = true;
                } else {
                    geom.setAttribute("color", new THREE.BufferAttribute(renderColors, 3));
                }
                painted = true;
            }
        }
        const m = active.beamSolidMesh.material;
        if (Array.isArray(m)) m.forEach((mat) => setVc(mat, painted));
        else if (m) setVc(m as THREE.Material, painted);

        if (active.beamSolidWarp && active.beamSolidBasePositions) {
            installBeamSolidWarp(
                active.mesh,
                active.beamSolidMesh,
                active.beamSolidBasePositions,
                active.beamSolidWarp,
                warpInfo?.field,
                warpInfo?.stepValues,
            );
        }
    }
}
