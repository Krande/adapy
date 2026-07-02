import {create} from "zustand";
import {persist} from "zustand/middleware";

// Admin-only opt-in for browser model-load instrumentation. When on,
// each REST-mode GLB load is timed phase-by-phase (IO / network / CPU /
// GPU), optionally self-profiled (JS Self-Profiling API — TS + WASM
// frames), and posted to the backend as an ``action='view'`` audit row.
// The "Frontend Loads" admin tab aggregates those rows.
//
// Default OFF and persisted to localStorage, so a load carries zero
// extra cost unless an admin explicitly turns it on. The collection is
// additionally gated on ``isAdmin`` at the call site (see loadMetrics).

export interface ViewMetricsState {
    // Master switch: time + post every model load.
    collectLoadMetrics: boolean;
    // Sub-switch: also run the JS Self-Profiling API during the load to
    // capture function-level (TS + WASM) self-time hotspots. Adds a
    // sampling cost, so it's separate from the basic phase timing.
    profileCalls: boolean;
    // Steady-state render profiling: samples every frame in the render
    // loop (frame time, draw calls, triangles, GPU ms via timer query)
    // and flushes one ``action='render'`` row per rolling window. More
    // intrusive than the one-shot load post (per-frame work), so it's a
    // separate opt-in.
    collectRenderMetrics: boolean;

    setCollectLoadMetrics: (v: boolean) => void;
    setProfileCalls: (v: boolean) => void;
    setCollectRenderMetrics: (v: boolean) => void;
}

export const useViewMetricsStore = create<ViewMetricsState>()(
    persist(
        (set) => ({
            collectLoadMetrics: false,
            profileCalls: true,
            collectRenderMetrics: false,
            setCollectLoadMetrics: (v) => set({collectLoadMetrics: v}),
            setProfileCalls: (v) => set({profileCalls: v}),
            setCollectRenderMetrics: (v) => set({collectRenderMetrics: v}),
        }),
        {name: "ada-view-metrics"},
    ),
);
