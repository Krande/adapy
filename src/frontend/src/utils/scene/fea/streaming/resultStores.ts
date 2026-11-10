// FEA streaming: the result session as the controls and the legend see it.
//
// Owns: registering what was just painted with the animation store -- what
// draws it, the source, the manifest, whether a result session is active, the
// step range and count, the derived warp scale -- and driving the shared
// legend off the field's range through the same contour resolver the paint
// kernels used. A field-less mesh deactivates the session and hides the
// legend. Nothing here fetches or paints, and nothing here is three.js: the
// result is named by its layer (viewport/api/resultLayer.ts), so the
// three.js viewport and the three.js-free one register a result the same way.
//
// Inputs: the layer, the source name + manifest, the field (or null), the
// user's field / reduction / colormap picks, the step, and the slider position
// when the caller is moving it. Reads and writes `useFeaAnimationStore` and
// `useColorStore`.

import type * as THREE from "three";

import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import type {ResultMeshLayer} from "@/viewport/api/resultLayer";
import type {LegendScope} from "../caseSteps";
import {resolveContourRange} from "../contourScale";
import {isPropertyField} from "../propertyColors";
import {isTimeHistory} from "../timeHistory";
import {selectedResultRange} from "../resultUnits";
import {autoWarpScale} from "../warpScale";
import {findDisplacementField} from "./warpSource";

export function syncResultStores(args: {
    /** What draws the result on the viewport on screen; the deformation scale is its influence. */
    layer: ResultMeshLayer;
    /** The three.js mesh behind the layer, for the features that still read it; null on a
     *  viewport without one. */
    mesh: THREE.Mesh | null;
    /** The diagonal of the undeformed model's box: what a deformation scale is chosen against.
     *  Asked for only when a field is shown. */
    modelSize: () => number;
    sourceName: string;
    manifest: FeaManifest;
    /** The field painted, or null for a field-less mesh. */
    field: FeaManifestField | null;
    fieldName: string | null;
    reduction: string | null;
    colormap: string;
    stepIndex: number;
    /** The sweep slider's own position, only when the caller is moving it. */
    sliderFactor: number | undefined;
    /** The field whose ``scalar_range`` was painted with, when it is not
     *  ``field`` itself: a load combination's view (its own range) or the
     *  field with the envelope's range. Defaults to ``field``. */
    rangeField?: FeaManifestField | null;
    /** Slots the step picker offers (stored steps + combinations). Defaults
     *  to the field's own step count. */
    nSlots?: number;
    /** What the range is measured over, for the legend to say. */
    legendScope?: {scope: LegendScope; label: string | null};
    /** The range the painters measured over what is shown, when they did. Takes the
     *  place of ``rangeField``'s ``scalar_range`` so the legend describes the same
     *  scale the elements were coloured on. */
    autoRange?: readonly [number, number] | null;
}): void {
    const {layer, mesh, sourceName, manifest, field, fieldName, reduction, colormap, stepIndex, sliderFactor} = args;
    const rangeField = args.rangeField ?? field;
    // Register the session with the animation store so
    // SimulationControls renders the deformation-scale slider /
    // play / stop instead of the GLTF-clip controls. Range follows
    // the field's analysis_kind: static = [0, 1] (one-directional),
    // eigen = [-1, +1] (mode shape has no inherent sign).
    const animStore = useFeaAnimationStore.getState();
    animStore.setMesh(mesh);
    if (useFeaAnimationStore.getState().resultLayer !== layer) animStore.setResultLayer(layer);
    animStore.setSourceName(sourceName);
    animStore.setManifest(manifest);
    if (field) {
        // Results present -> activate the FEA session (SimulationControls: step slider / field
        // selector / warp). Range follows analysis_kind: static = [0, 1], eigen = [-1, +1].
        animStore.setSessionActive(true);
        const range: [number, number] = field.analysis_kind === "eigen" ? [-1, 1] : [0, 1];
        animStore.setRange(range);
        animStore.setTimeHistory(isTimeHistory(field, manifest));
        // Only when the caller asked. See ``sliderFactor`` on the argument type:
        // the influence and the slider are different numbers, and equating them
        // moved the indicator on every component change.
        if (sliderFactor !== undefined) animStore.setFactor(sliderFactor);
        animStore.setStepIndex(stepIndex);
        animStore.setNSteps(args.nSlots ?? field.n_steps);
        animStore.setLegendScope(args.legendScope?.scope ?? "all", args.legendScope?.label ?? null);
        // A deformation scale the model can be seen at. Derived from the
        // displacement field and the model size, and only ever applied while the
        // user has not set a scale of their own.
        {
            // Base positions do not change, so the size is the same answer every
            // time: a number that changes on its own is worse than a number that
            // is slightly off.
            animStore.applyAutoScaleFactor(
                autoWarpScale(findDisplacementField(manifest), args.modelSize()),
            );
            // A fresh load (the caller moved the slider) was painted before the
            // scale above existed, so its influence is the bare slider value. Put
            // the result where the controls now say it is -- slider times scale --
            // or the first view of a deck that needed scaling showed it unscaled
            // until something happened to repaint it.
            if (sliderFactor !== undefined) {
                layer.setInfluence(sliderFactor * useFeaAnimationStore.getState().scaleFactor);
            }
        }
        animStore.setFieldName(fieldName);
        if (reduction != null) animStore.setReduction(reduction);
        animStore.setColormap(colormap);
        // Through the same resolver the kernels used: a pinned range the legend
        // does not know about is a legend that disagrees with the picture beside
        // it, which is worse than no legend at all.
        // A property ignores the result scale's pinned ends, as its painter does.
        const property = isPropertyField(field);
        animStore.setAutoRange(!property && args.autoRange ? [args.autoRange[0], args.autoRange[1]] : null);
        const [legendMin, legendMax] = resolveContourRange(
            (!property && args.autoRange) || selectedResultRange(rangeField, reduction ?? "magnitude"),
            property ? null : useFeaAnimationStore.getState().contour,
        );
        const legendStore = useColorStore.getState();
        legendStore.setMin(legendMin);
        legendStore.setMax(legendMax);
        legendStore.setShowLegend(true);
    } else {
        // Field-less FEM mesh (model only): no results -> NO simulation session, so the
        // result controls + the results-only "show in data" action stay hidden; the
        // Simulation panel opens on its Model tab (the manifest set above is the FE
        // model). The beam-solids toggle acts on the session's FEA mesh, not on a
        // result session, so it still works from there.
        animStore.setSessionActive(false);
        animStore.setFieldName(null);
        animStore.setNSteps(1);
        animStore.setStepIndex(0);
        animStore.setTimeHistory(false);
        useColorStore.getState().setShowLegend(false);
    }
}
