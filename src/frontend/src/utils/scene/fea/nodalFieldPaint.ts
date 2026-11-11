// One step of a nodal field as plain numbers: a colour and a displacement per node.
//
// This is the computation behind a painted result, with nothing of a renderer in it: field values
// go in, two typed arrays come out (linear RGB in 0..1 and the un-scaled displacement, three floats per
// node each, in the mesh's own node numbering). Whichever viewport is on screen draws them through
// a result layer (viewport/api/resultLayer.ts); the deformation scale is the layer's influence, so
// it is not applied here and a change of scale repaints nothing.
//
// No three.js in this module or in anything it imports: the three.js-free viewport uses it too.

import type {FeaManifestField, FeaScalarRange} from "@/services/viewerApi";
import {getColormap, linearColormap} from "./colormaps";
import {bandedColormap, resolveContourRange, type ContourSettings} from "./contourScale";
import {translationOffsets, warpValue} from "./warpComponents";

export interface NodalFieldPaint {
    /** Nodes of the mesh: both outputs hold three floats for each. */
    nPoints: number;
    /** Field driving the per-node colour. */
    colorField: FeaManifestField;
    /** Step values for ``colorField``, length ``nPoints * colorField.components.length``. */
    colorStepValues: Float32Array;
    /** "magnitude" or one of ``colorField.components``. */
    reduction: string;
    /** Field driving the displacement. Omit to leave the shape undeformed: the displacement is
     *  then all zero, so a layer still has a morph to scale. */
    warpField?: FeaManifestField;
    /** Step values for ``warpField``, length ``nPoints * warpField.components.length``. Only the
     *  translation components are read (see ``translationOffsets``). */
    warpStepValues?: Float32Array;
    /** Colormap ID, one of the keys in ``colormaps.COLORMAPS``; unknown or missing is viridis. */
    colormap?: string;
    /** How the scale is drawn: discrete bands, and either end of the range pinned. */
    contour?: ContourSettings | null;
    /** The range the scale follows where ``contour`` pins no end. Omitted: the field's
     *  ``scalar_range``. */
    autoRange?: readonly [number, number] | null;
}

/** The range of `reduction` in the field's own ``scalar_range``. */
export function fieldScalarRange(field: FeaManifestField, reduction: string): [number, number] {
    const range: FeaScalarRange = field.scalar_range;
    const r = range[reduction];
    if (r) return [r[0], r[1]];
    if (field.components.length > 0) {
        const fallback = range[field.components[0]];
        if (fallback) return [fallback[0], fallback[1]];
    }
    return [0, 1];
}

function componentIndex(field: FeaManifestField, reduction: string): number {
    const index = field.components.indexOf(reduction);
    // A one-component field's reduction is the bake's "scalar" (default_view for any
    // field under three components without presentation metadata) rather than the
    // component's own name. It means "the value itself": component 0. Without this
    // every such field fell through to the 0 fallback and painted uniformly at the
    // bottom of its scale.
    if (index < 0 && field.components.length === 1) return 0;
    return index;
}

/**
 * Write the step's colours into `outColors` and its displacement into `outDisplacement` (both
 * ``nPoints * 3`` long, overwritten in full). One pass over the nodes and no allocation: the
 * caller owns the two arrays and reuses them across steps.
 */
export function paintNodalField(args: NodalFieldPaint, outDisplacement: Float32Array, outColors: Float32Array): void {
    const {nPoints: n_points, colorField, colorStepValues, reduction, warpField, warpStepValues, contour, autoRange} = args;
    // Linear light: a viewport draws the legend's colour only when it is handed that (colormaps.ts).
    const colormap = linearColormap(bandedColormap(getColormap(args.colormap), contour?.levels ?? null));
    const n_components = colorField.components.length;
    if (colorStepValues.length !== n_points * n_components) {
        throw new Error(
            `applyFieldToMesh: colorStepValues length ${colorStepValues.length} doesn't match ` +
            `n_points*n_components (${n_points}*${n_components}=${n_points * n_components})`,
        );
    }

    // Warp source — independent of the colour source. ``warpField`` is
    // typically the manifest's displacement field; ``colorField`` is
    // whatever the user picked to visualise. When both are the same
    // (the user picked displacement), this still works — the loop
    // reads two component slices from the same backing array.
    let warpComponents = 0;
    // Which slots inside one point's record are the translation. NOT always the
    // first three -- see translationOffsets.
    let warpAxes: [number, number, number] = [0, 1, 2];
    if (warpField && warpStepValues) {
        warpComponents = warpField.components.length;
        warpAxes = translationOffsets(warpField);
        if (warpStepValues.length !== n_points * warpComponents) {
            throw new Error(
                `applyFieldToMesh: warpStepValues length ${warpStepValues.length} doesn't match ` +
                `n_points*warpComponents (${n_points}*${warpComponents}=${n_points * warpComponents})`,
            );
        }
    }

    const isMagnitude = reduction === "magnitude";
    const compIdx = isMagnitude ? -1 : componentIndex(colorField, reduction);

    // Always full-length even when no warp source is active: the deltas are
    // zero, so the shape sits at its base positions whatever the scale.
    outDisplacement.fill(0);

    const [rangeMin, rangeMax] = resolveContourRange(autoRange ?? fieldScalarRange(colorField, reduction), contour);
    const range = rangeMax - rangeMin;
    const scaleColor = range > 0 ? 1 / range : 0;

    for (let v = 0; v < n_points; v++) {
        const base = v * 3;
        const colorStride = v * n_components;

        if (warpComponents > 0 && warpStepValues) {
            const warpStride = v * warpComponents;
            outDisplacement[base + 0] = warpValue(warpStepValues, warpStride, warpAxes[0]);
            outDisplacement[base + 1] = warpValue(warpStepValues, warpStride, warpAxes[1]);
            outDisplacement[base + 2] = warpValue(warpStepValues, warpStride, warpAxes[2]);
        }

        let scalar: number;
        if (isMagnitude) {
            // Magnitude of the *colour* field's first 3 components.
            // For a vector3 displacement this is ||u||; for a
            // displacement-warped scalar field it'd reduce to the
            // value itself (n_components == 1).
            const dx0 = colorStepValues[colorStride] || 0;
            const dy0 = n_components >= 2 ? colorStepValues[colorStride + 1] || 0 : 0;
            const dz0 = n_components >= 3 ? colorStepValues[colorStride + 2] || 0 : 0;
            scalar = Math.sqrt(dx0 * dx0 + dy0 * dy0 + dz0 * dz0);
        } else if (compIdx >= 0) {
            scalar = colorStepValues[colorStride + compIdx] || 0;
        } else {
            scalar = colorStepValues[colorStride] || 0;
        }
        const t = isFinite(scalar) ? (scalar - rangeMin) * scaleColor : 0;
        colormap(t, outColors, base);
    }
}
