import {create} from "zustand";
import {persist} from "zustand/middleware";

// Perf toggles for A/B-testing the Phase A render-cost changes
// (material choice, backface culling, MSAA, DPR, on-demand render,
// content hides). Persisted to localStorage so a chosen config
// survives reloads while the user is benchmarking.
//
// Each flag's default reproduces the pre-toggle behaviour so flipping
// none of them leaves the viewer exactly as it was.

export type MaterialMode = "standard" | "lambert";

export interface PerfState {
    // FEA material gating ----------------------------------------------------
    materialMode: MaterialMode;
    solidsBackfaceCull: boolean;
    solidsSmoothShading: boolean;

    // Renderer gating --------------------------------------------------------
    disableShadowMap: boolean;
    // ``antialias`` requires renderer recreation; the Performance panel
    // shows a "(reload required)" badge next to this toggle and the
    // ThreeCanvas reads the value at construction time only.
    antialias: boolean;
    // ``pixelRatioCap`` is applied via setPixelRatio on every render
    // setup and during the adaptive-DPR transitions.
    pixelRatioCap: number;
    adaptivePixelRatio: boolean;
    onDemandRender: boolean;

    // FEA content gating -----------------------------------------------------
    hideBeamSolids: boolean;
    hideElementEdges: boolean;

    // Picking ---------------------------------------------------------------
    // Flat-varying picker: builds an INDEXED picker geometry where
    // each triangle has its three corners' picked color taken from a
    // single newly-duplicated "provoking" vertex (using GLSL3 flat
    // varying). Original mesh vertices are reused for the other two
    // corners — cuts picker memory roughly 30-50% vs the default
    // non-indexed-per-triangle layout. Takes effect on next mesh
    // load; existing picker meshes keep whichever mode they were
    // built with.
    useFlatPicker: boolean;

    // Loading ---------------------------------------------------------------
    // Time-slice the per-mesh model-prepare loop: process a few-ms budget of
    // meshes per animation frame, yielding to the browser between batches so
    // the main thread never blocks in one long stall. The model streams into
    // the scene progressively and the viewer stays interactive during load
    // (no freeze). Total work is unchanged; only its scheduling.
    timeSlicedLoad: boolean;

    // Setters ----------------------------------------------------------------
    setMaterialMode: (v: MaterialMode) => void;
    setSolidsBackfaceCull: (v: boolean) => void;
    setSolidsSmoothShading: (v: boolean) => void;
    setDisableShadowMap: (v: boolean) => void;
    setAntialias: (v: boolean) => void;
    setPixelRatioCap: (v: number) => void;
    setAdaptivePixelRatio: (v: boolean) => void;
    setOnDemandRender: (v: boolean) => void;
    setHideBeamSolids: (v: boolean) => void;
    setHideElementEdges: (v: boolean) => void;
    setUseFlatPicker: (v: boolean) => void;
    setTimeSlicedLoad: (v: boolean) => void;
}

export const usePerfStore = create<PerfState>()(
    persist(
        (set) => ({
            materialMode: "standard",
            solidsBackfaceCull: false,
            solidsSmoothShading: false,

            disableShadowMap: false,
            antialias: true,
            pixelRatioCap: 1.0,
            adaptivePixelRatio: false,
            onDemandRender: false,

            hideBeamSolids: false,
            hideElementEdges: false,

            useFlatPicker: false,

            timeSlicedLoad: false,

            setMaterialMode: (v) => set({materialMode: v}),
            setSolidsBackfaceCull: (v) => set({solidsBackfaceCull: v}),
            setSolidsSmoothShading: (v) => set({solidsSmoothShading: v}),
            setDisableShadowMap: (v) => set({disableShadowMap: v}),
            setAntialias: (v) => set({antialias: v}),
            setPixelRatioCap: (v) => set({pixelRatioCap: v}),
            setAdaptivePixelRatio: (v) => set({adaptivePixelRatio: v}),
            setOnDemandRender: (v) => set({onDemandRender: v}),
            setHideBeamSolids: (v) => set({hideBeamSolids: v}),
            setHideElementEdges: (v) => set({hideElementEdges: v}),
            setUseFlatPicker: (v) => set({useFlatPicker: v}),
            setTimeSlicedLoad: (v) => set({timeSlicedLoad: v}),
        }),
        {name: "ada-perf"},
    ),
);

// Snapshot of every performance option (the data fields of the store, setters
// excluded) for the frontend load/render audit logs — so a metrics row records
// exactly which perf toggles were active. Auto-collects new options, so adding a
// field to PerfState includes it here without touching the audit code.
export function perfOptionsSnapshot(): Record<string, unknown> {
    const state = usePerfStore.getState() as unknown as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(state)) {
        if (typeof value !== "function") out[key] = value; // skip the set* actions
    }
    return out;
}

// Imperative dirty-flag helper for on-demand render mode. Modules
// that mutate scene state outside of OrbitControls / animation
// drivers (e.g. field-apply, manifest swap) can call this to nudge
// the next frame even if no controls event fires. The render loop
// reads + clears the flag once per animate tick.
let _dirty = true;
export function requestRender(): void {
    _dirty = true;
}
export function consumeDirty(): boolean {
    const wasDirty = _dirty;
    _dirty = false;
    return wasDirty;
}
