import {create} from "zustand";

import type * as THREE from "three";
import type {FeaManifest} from "@/services/viewerApi";

/** State for the streaming-FEA viewer's two-slider control surface:
 *
 *   * Slider A (step / mode index) — discrete; lives in
 *     ``stepIndex`` and is driven by the picker.
 *   * Slider B (deformation scale) — continuous; lives in ``factor``.
 *     Manual drags set ``factor`` directly, the play / pause / stop
 *     buttons drive the RAF oscillator instead.
 *
 * Range of ``factor`` follows the field's ``analysis_kind``: static
 * = [0, 1] (one-directional displacement, signed sweep isn't
 * physical), eigen = [-1, +1] (mode shape has no inherent sign).
 *
 * The RAF driver lives in ``feaAnimationDriver.ts`` and writes
 * ``mesh.morphTargetInfluences[0]`` directly — the store is just
 * the UI state. We don't go through ``THREE.AnimationMixer`` /
 * ``animationStore``: those are tied to GLTF clips and the user
 * flagged that pipeline as fragile, so this slice is standalone.
 */
export interface FeaAnimationState {
    /** Whether a streaming-FEA session is currently active in the
     * scene. Set when ``load_fea_streaming`` runs; cleared when the
     * scene is replaced or the FEA picker is closed. While false,
     * SimulationControls can fall back to its GLTF-clip path. */
    sessionActive: boolean;

    /** The mesh whose ``morphTargetInfluences[0]`` we drive. Set by
     * ``load_fea_streaming`` after applyFieldToMesh has installed
     * the morph attribute. The RAF driver reads this. */
    mesh: THREE.Mesh | null;

    /** Range of the deformation factor. Inferred from the active
     * field's ``analysis_kind`` at apply time. */
    range: [number, number];

    /** Current deformation factor. Slider value when paused; sweep
     * output when playing. Always in ``range``. */
    factor: number;

    /** Oscillation period in seconds. */
    period: number;

    /** True while the RAF driver is sweeping ``factor``. False
     * while paused / stopped. */
    isPlaying: boolean;

    /** Active step / mode index. The picker updates this and
     * applyFieldToMesh re-runs to swap the morph delta. */
    stepIndex: number;

    /** Total number of steps in the active field. Picker source of
     * truth; SimulationControls uses it as the slider's max. */
    nSteps: number;

    /** Source key (storage-relative path) of the active session.
     * SimulationControls' field / reduction selectors need this to
     * re-call ``load_fea_streaming`` when the user changes the
     * field. */
    sourceName: string | null;

    /** Manifest of the active source. The viewer fetches this once
     * on toggle (via ``load_fea_with_defaults``) and keeps it here
     * so SimulationControls can read field metadata + step values
     * without re-hitting the server. */
    manifest: FeaManifest | null;

    /** Currently-displayed field's canonical name. */
    fieldName: string | null;

    /** Reduction within the field — "magnitude" or a component name.
     * Drives the colour LUT in ``applyFieldToMesh``. */
    reduction: string;

    /** Active colormap ID — one of the keys in ``COLORMAPS``
     * (utils/scene/fea/colormaps.ts). Drives the per-vertex colour
     * sampling in ``applyFieldToMesh``. Changing this re-applies the
     * active step via ``applyStep`` so the displayed colours update
     * without a re-fetch. */
    colormap: string;

    /** Step-change callback registered by ``load_fea_streaming``.
     * SimulationControls calls this when the user drags the step
     * slider; the closure runs another ``load_fea_streaming`` with
     * the updated stepIndex. */
    applyStep: ((stepIndex: number) => Promise<void>) | null;

    setSessionActive: (active: boolean) => void;
    setMesh: (mesh: THREE.Mesh | null) => void;
    setRange: (range: [number, number]) => void;
    setFactor: (factor: number) => void;
    setPeriod: (period: number) => void;
    setIsPlaying: (playing: boolean) => void;
    setStepIndex: (i: number) => void;
    setNSteps: (n: number) => void;
    setSourceName: (s: string | null) => void;
    setManifest: (m: FeaManifest | null) => void;
    setFieldName: (n: string | null) => void;
    setReduction: (r: string) => void;
    setColormap: (c: string) => void;
    setApplyStep: (cb: ((stepIndex: number) => Promise<void>) | null) => void;
    /** Reset to inactive — called when the scene is replaced. */
    reset: () => void;
}

const DEFAULT_RANGE: [number, number] = [-1, 1];
const DEFAULT_PERIOD = 2.0;

export const useFeaAnimationStore = create<FeaAnimationState>((set) => ({
    sessionActive: false,
    mesh: null,
    range: DEFAULT_RANGE,
    factor: 1.0,
    period: DEFAULT_PERIOD,
    isPlaying: false,
    stepIndex: 0,
    nSteps: 0,
    sourceName: null,
    manifest: null,
    fieldName: null,
    reduction: "magnitude",
    colormap: "viridis",
    applyStep: null,

    setSessionActive: (active) => set({sessionActive: active}),
    setMesh: (mesh) => set({mesh}),
    setRange: (range) => set({range}),
    setFactor: (factor) => set({factor}),
    setPeriod: (period) => set({period: Math.max(0.1, period)}),
    setIsPlaying: (isPlaying) => set({isPlaying}),
    setStepIndex: (stepIndex) => set({stepIndex}),
    setNSteps: (nSteps) => set({nSteps}),
    setSourceName: (sourceName) => set({sourceName}),
    setManifest: (manifest) => set({manifest}),
    setFieldName: (fieldName) => set({fieldName}),
    setReduction: (reduction) => set({reduction}),
    setColormap: (colormap) => set({colormap}),
    setApplyStep: (cb) => set({applyStep: cb}),
    reset: () =>
        set({
            sessionActive: false,
            mesh: null,
            range: DEFAULT_RANGE,
            factor: 1.0,
            isPlaying: false,
            stepIndex: 0,
            nSteps: 0,
            sourceName: null,
            manifest: null,
            fieldName: null,
            reduction: "magnitude",
            // Don't reset ``colormap`` on scene clear — it's a per-user
            // preference, not per-session. Users who picked Abaqus
            // rainbow once want it to stick across model swaps.
            applyStep: null,
        }),
}));
