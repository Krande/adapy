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

    /** Whether to drive mesh deformation from the displacement field.
     * Default true (Abaqus CAE behaviour — stresses on the deformed
     * shape). For reaction-force fields the warp is force-off
     * regardless of this flag; for displacement the flag controls
     * whether the user sees the deformed shape at all. Persisted
     * across scene swaps like ``colormap`` — it's a per-user
     * preference, not per-session. */
    warpEnabled: boolean;

    /** Multiplier applied on top of ``factor`` before it lands on the
     *  morph influence. Default 1 (no amplification). Drives the
     *  "warp scale" knob in the transport bar — analogous to
     *  Abaqus/Paraview's deformation-scale factor. Useful when the
     *  raw displacement field is tiny (sub-mm on a 100 m structure)
     *  and the user wants to exaggerate the deformed shape without
     *  changing the underlying field values. */
    scaleFactor: number;

    /** Layer filter for element fields with multi-IP shell stacks.
     *  ``top``/``bottom``/``mid`` pick the matching IPs out of the
     *  bucket's ``ip_layout``; ``all`` keeps every IP. Unused for
     *  single-IP solid elements (every IP carries the same ``layer``
     *  marker so the picker collapses to ``all``). Persisted across
     *  ``reset()`` — engineers picking "bottom layer for SIF" once
     *  want it sticky across model swaps. */
    layer: string;

    /** Reduction applied across the IPs inside the picked layer to
     *  collapse each element's (n_ips × n_components) block down to
     *  one scalar per element per component. ``max_abs`` matches the
     *  Sesam / Abaqus stress-output convention for "outer fibre value
     *  of interest"; ``mean`` is the engineering average; ``max`` /
     *  ``min`` are useful when the user knows the field's sign
     *  convention. Persisted across ``reset()``. */
    ipReduction: string;

    /** Smooth-shade element fields by averaging each vertex's element
     *  scalars across the elements that touch it. ``false`` (default)
     *  paints the same colour onto every vertex of an element — the
     *  raw piecewise-constant field. ``true`` gives the
     *  Abaqus/Paraview "averaged at nodes" look at the cost of
     *  hiding inter-element discontinuities. Persisted across
     *  ``reset()`` like the other per-user preferences. Unused for
     *  nodal fields (they're already smooth by construction). */
    nodalAverage: boolean;

    /** Show beam (line) elements as extruded 3D solids instead of
     *  just LineSegments. Only meaningful when the manifest carries
     *  a ``beam_solids_url`` (SIF bakes with section info). ``false``
     *  (default) preserves the existing line-only beam render — the
     *  solid mesh stays loaded but invisible. Persisted across
     *  ``reset()`` so user pref sticks across scene swaps. */
    beamSolidsVisible: boolean;

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
    setWarpEnabled: (enabled: boolean) => void;
    setScaleFactor: (s: number) => void;
    setLayer: (layer: string) => void;
    setIpReduction: (r: string) => void;
    setNodalAverage: (smooth: boolean) => void;
    setBeamSolidsVisible: (visible: boolean) => void;
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
    // Abaqus rainbow as the default — matches what most CAE users
    // expect from a stress / displacement plot. Viridis lives one
    // dropdown away in the SimulationControls options panel.
    colormap: "abaqus",
    // Warp on by default — most users picking a stress field want it
    // shown on the deformed shape (Abaqus / Paraview default).
    warpEnabled: true,
    // Identity scale by default — exaggeration is an explicit opt-in.
    scaleFactor: 1.0,
    // Default layer / IP reduction mirror the bake's
    // ``default_view.layer`` / ``ip_reduction`` keys (artefacts.py
    // build_manifest). When a nodal field is active these are
    // ignored, but keeping non-null defaults means a switch from
    // nodal → element doesn't briefly render the mesh with an
    // undefined reduction.
    layer: "top",
    ipReduction: "max_abs",
    // Flat per-element shading by default — that's the raw signal.
    // Smooth averaging is an explicit opt-in because it can hide
    // inter-element discontinuities that some users want to see.
    nodalAverage: false,
    // Line-only beam render by default — matches the pre-Phase-5
    // behaviour. Users opt into the solid render via the gear panel.
    beamSolidsVisible: false,
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
    setWarpEnabled: (warpEnabled) => set({warpEnabled}),
    setScaleFactor: (scaleFactor) => set({scaleFactor}),
    setLayer: (layer) => set({layer}),
    setIpReduction: (ipReduction) => set({ipReduction}),
    setNodalAverage: (nodalAverage) => set({nodalAverage}),
    setBeamSolidsVisible: (beamSolidsVisible) => set({beamSolidsVisible}),
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
            // Don't reset ``colormap``, ``warpEnabled``, ``layer``,
            // ``ipReduction``, ``nodalAverage``, or
            // ``beamSolidsVisible`` on scene clear — all six are
            // per-user preferences, not per-session. Users who picked
            // Abaqus rainbow + warp-off + bottom-layer-max-abs +
            // smooth + solid-beams once want them to stick across
            // model swaps.
            applyStep: null,
        }),
}));
