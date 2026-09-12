// FEA streaming: painting an element field (AFEL).
//
// Owns: range-fetching one step per element-type bucket, reducing it to one
// scalar per element and writing vertex colours through the AFEM draw ranges
// on the result mesh and, when present, the beam-solid mesh -- then putting
// the lerped nodal warp back on the solids, which the element paint had
// installed as a zero delta. Every surface carrying the field is painted with
// the same reduction, colormap, contour range and morph influence.
//
// Inputs: the session handle, the field + step, the user's view settings, the
// resolved warp source, and the blob fetchers. Reads the layer / IP-reduction /
// nodal-average toggles from `useFeaAnimationStore`.

import type {ContourSettings} from "../contourScale";
import type {FeaFetcher, FeaRangeFetcher} from "@/services/fea/feaFetcher";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import type {FeaManifestField} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import type {FeaSessionHandle} from "@/state/modelSession";
import {applyElemFieldToMesh} from "../applyElemField";
import {installBeamSolidWarp} from "./warp";

export async function paintElemField(args: {
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
    // Element-field render path (AFEL). Range-fetch one step per
    // element-type bucket in parallel; the bake guarantees parallel
    // step counts across buckets within a logical field, so the same
    // ``stepIndex`` indexes every bucket. The reduction kernel
    // collapses (n_ips × n_components) → 1 scalar per element and
    // writes vertex colours via AFEM draw ranges.
    // The caller routes here only for a field with buckets; an empty list
    // paints nothing rather than crashing on a bake that lied.
    const buckets = field.per_type ?? [];
    const perTypeStepValues = await Promise.all(
        buckets.map((bk, i) =>
            fetchElemFieldStep(rangeFetcher, fetcher, bk, stepIndex, cacheKey).catch((err) => {
                throw new Error(
                    `element field ${field.name_canonical} bucket ${buckets[i].elem_type} ` +
                    `step ${stepIndex}: ${err instanceof Error ? err.message : String(err)}`,
                );
            }),
        ),
    );
    const {layer, ipReduction, nodalAverage} = useFeaAnimationStore.getState();
    applyElemFieldToMesh({
        mesh: active.mesh,
        basePositions: active.basePositions,
        colorField: field,
        perTypeStepValues,
        layer,
        ipReduction,
        reduction: reductionStr,
        warpField: warpInfo?.field,
        warpStepValues: warpInfo?.stepValues,
        displacementScale,
        colormap,
        contour,
        nodalAverage,
        // Only where the deck cannot show beam solids. Where it can, the beam
        // carries its result on its own surface, and a coloured line as well
        // puts two renderings of one beam in the same place -- the black
        // element-edge overlay against the coloured line, neither legible.
        // Always build them. Which of the two renderings you SEE is a
        // visibility question, not a build-time one -- gating on whether the
        // bake carried solids meant a deck that had them showed black beams
        // the moment you switched the solids off.
        lineFallback: true,
    });
    // Beam-solid mesh — paint with the same AFEL data. Beam
    // labels appear in both drawRanges maps, but the main-mesh
    // entries have zero triangles (line elements) so the kernel
    // is a no-op there for beams, and the beam-solid mesh has no
    // entries for shells. Net effect: each label paints exactly
    // the mesh that owns its triangles. Smooth shading skipped:
    // each beam has at most one IP along its length so per-
    // element colour and nodal-averaged colour coincide.
    //
    // Note: applyElemFieldToMesh installs a zero-magnitude morph
    // delta (no warp arg here). ``installBeamSolidWarp`` below
    // overwrites that with the lerped nodal warp so the solid
    // beams stay connected to the deformed structure under any
    // morph-scale factor.
    //
    // The SAME influence as the main mesh, passed explicitly. After the
    // first apply the beam-solid mesh shares the main mesh's
    // ``morphTargetInfluences`` array (installBeamSolidWarp links them), so
    // the influence this call writes lands on the main mesh too. Left to
    // its default of 1 it reset the whole model to an unscaled warp on
    // every element-field repaint -- which is what the warp toggle, a
    // component change or a colormap change all are. With an auto-derived
    // scale of 50 on a deck deforming by millimetres, a warp at 1 cannot be
    // told from no warp at all, and the toggle looked dead.
    if (active.beamSolidMesh && active.beamSolidBasePositions) {
        applyElemFieldToMesh({
            mesh: active.beamSolidMesh,
            basePositions: active.beamSolidBasePositions,
            colorField: field,
            perTypeStepValues,
            layer,
            ipReduction,
            reduction: reductionStr,
            displacementScale,
            colormap,
            contour,
            nodalAverage: false,
        });
        if (active.beamSolidWarp) {
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
