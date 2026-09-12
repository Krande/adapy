// FEA streaming: the result session as the controls and the legend see it.
//
// Owns: registering what was just painted with the animation store -- the
// mesh, the source, the manifest, whether a result session is active, the
// step range and count, the derived warp scale -- and driving the shared
// legend off the field's range through the same contour resolver the paint
// kernels used. A field-less mesh deactivates the session and hides the
// legend. Nothing here fetches or paints; the colour-owner bookkeeping and
// the step callback stay with the loader that owns the closure.
//
// Inputs: the painted mesh, the source name + manifest, the field (or null),
// the user's field / reduction / colormap picks, the step, and the slider
// position when the caller is moving it. Reads and writes
// `useFeaAnimationStore` and `useColorStore`.

import * as THREE from "three";

import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {resolveContourRange} from "../contourScale";
import {selectedResultRange} from "../resultUnits";
import {autoWarpScale} from "../warpScale";
import {findDisplacementField} from "./warp";

export function syncResultSession(args: {
    mesh: THREE.Mesh;
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
}): void {
    const {mesh, sourceName, manifest, field, fieldName, reduction, colormap, stepIndex, sliderFactor} = args;
    // Register the session with the animation store so
    // SimulationControls renders the deformation-scale slider /
    // play / stop instead of the GLTF-clip controls. Range follows
    // the field's analysis_kind: static = [0, 1] (one-directional),
    // eigen = [-1, +1] (mode shape has no inherent sign).
    const animStore = useFeaAnimationStore.getState();
    animStore.setMesh(mesh);
    animStore.setSourceName(sourceName);
    animStore.setManifest(manifest);
    if (field) {
        // Results present -> activate the FEA session (SimulationControls: step slider / field
        // selector / warp). Range follows analysis_kind: static = [0, 1], eigen = [-1, +1].
        animStore.setSessionActive(true);
        const range: [number, number] = field.analysis_kind === "eigen" ? [-1, 1] : [0, 1];
        animStore.setRange(range);
        // Only when the caller asked. See ``sliderFactor`` on the argument type:
        // the influence and the slider are different numbers, and equating them
        // moved the indicator on every component change.
        if (sliderFactor !== undefined) animStore.setFactor(sliderFactor);
        animStore.setStepIndex(stepIndex);
        animStore.setNSteps(field.n_steps);
        // A deformation scale the model can be seen at. Derived from the
        // displacement field and the model size, and only ever applied while the
        // user has not set a scale of their own.
        {
            const geom = mesh.geometry;
            // Recompute rather than trust a cached box: a stale one from an
            // earlier state made the derived scale wobble between field
            // switches, and a number that changes on its own is worse than a
            // number that is slightly off. Base positions do not change, so
            // this is the same answer every time.
            geom.computeBoundingBox();
            const size = geom.boundingBox
                ? geom.boundingBox.min.distanceTo(geom.boundingBox.max)
                : 0;
            animStore.applyAutoScaleFactor(
                autoWarpScale(findDisplacementField(manifest), size),
            );
            // A fresh load (the caller moved the slider) was painted before the
            // scale above existed, so its influence is the bare slider value. Put
            // the mesh where the controls now say it is -- slider times scale --
            // or the first view of a deck that needed scaling showed it unscaled
            // until something happened to repaint it.
            if (sliderFactor !== undefined && mesh.morphTargetInfluences) {
                mesh.morphTargetInfluences[0] =
                    sliderFactor * useFeaAnimationStore.getState().scaleFactor;
            }
        }
        animStore.setFieldName(fieldName);
        if (reduction != null) animStore.setReduction(reduction);
        animStore.setColormap(colormap);
        // Through the same resolver the kernels used: a pinned range the legend
        // does not know about is a legend that disagrees with the picture beside
        // it, which is worse than no legend at all.
        const [legendMin, legendMax] = resolveContourRange(
            selectedResultRange(field, reduction ?? "magnitude"),
            useFeaAnimationStore.getState().contour,
        );
        const legendStore = useColorStore.getState();
        legendStore.setMin(legendMin);
        legendStore.setMax(legendMax);
        legendStore.setShowLegend(true);
    } else {
        // Field-less FEM mesh (model only): no results -> NO simulation session, so
        // SimulationControls + the results-only "show in data" action stay hidden. The
        // beam-solids toggle acts on the session's FEA mesh, not on a result session, so
        // it still works from the Scene > FEM panel.
        animStore.setSessionActive(false);
        animStore.setFieldName(null);
        animStore.setNSteps(1);
        animStore.setStepIndex(0);
        useColorStore.getState().setShowLegend(false);
    }
}
