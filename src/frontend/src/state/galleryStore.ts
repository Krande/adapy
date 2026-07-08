import {create} from "zustand";
import {persist} from "zustand/middleware";

// Gallery mode: a small prev/next HUD. The "files" walk cycles the
// current scope's loadable FILES one at a time (clear → load). The
// "geoms" walk cycles the GEOMS already in the scene, selecting +
// framing each in turn (a slideshow of the model's parts), in one of
// three orders:
//   - "scene": scene/batched-mesh traversal order (roughly load order).
//   - "density": triangles per surface area — heaviest/most-detailed
//     geometry first.
//   - "tree": the model tree's hierarchy order (parent → children DFS).
//   - "distorted": only geoms with a "crows-nest" spike (a thin triangle
//     shooting out past the geometry), worst-first — the tessellation-bug
//     inspector. Forces geometry edges on so the spikes are visible.
// "hideUnselected" optionally isolates the current geom during a geom
// walk (hides everything else) so it reads clearly.
//
// The enabled flag and the walk preferences persist (viewing
// preferences); the per-walk index is transient and lives in the
// GalleryControls component (it depends on the current scope / scene).
export type GalleryWalk = "files" | "geoms";
export type GeomWalkOrder = "scene" | "density" | "tree" | "distorted";

interface GalleryState {
    enabled: boolean;
    setEnabled: (v: boolean) => void;
    toggle: () => void;

    walk: GalleryWalk;
    setWalk: (w: GalleryWalk) => void;

    geomOrder: GeomWalkOrder;
    setGeomOrder: (o: GeomWalkOrder) => void;

    hideUnselected: boolean;
    setHideUnselected: (v: boolean) => void;
    toggleHideUnselected: () => void;

    // Measured height (px) of the mobile gallery bar so bottom-anchored overlays (the audit toast)
    // can stack ABOVE it instead of overlapping. 0 when the bar isn't shown (desktop / gallery off).
    // Transient — not persisted.
    mobileBarHeight: number;
    setMobileBarHeight: (h: number) => void;
}

export const useGalleryStore = create<GalleryState>()(
    persist(
        (set) => ({
            enabled: false,
            setEnabled: (v) => set({enabled: v}),
            toggle: () => set((s) => ({enabled: !s.enabled})),

            walk: "files",
            setWalk: (walk) => set({walk}),

            geomOrder: "scene",
            setGeomOrder: (geomOrder) => set({geomOrder}),

            hideUnselected: false,
            setHideUnselected: (hideUnselected) => set({hideUnselected}),
            toggleHideUnselected: () => set((s) => ({hideUnselected: !s.hideUnselected})),

            mobileBarHeight: 0,
            setMobileBarHeight: (mobileBarHeight) => set({mobileBarHeight}),
        }),
        {
            name: "ada-gallery",
            // Persist only the viewing preferences; mobileBarHeight is transient runtime layout.
            partialize: (s) => ({
                enabled: s.enabled,
                walk: s.walk,
                geomOrder: s.geomOrder,
                hideUnselected: s.hideUnselected,
            }),
        },
    ),
);
