import {create} from "zustand";
import {persist} from "zustand/middleware";

// Gallery mode: a small prev/next HUD. The classic walk cycles the
// current scope's loadable FILES one at a time (clear → load). Two
// more walk types cycle the GEOMS already in the scene, selecting +
// framing each in turn (a slideshow of the model's parts):
//   - "geoms": every draw-range in the scene, ordered by "scene"
//     (load order) or "density" (triangles per surface area — the
//     heaviest/most-detailed geometry first).
//   - "tree": the same geoms but in the model tree's hierarchy order.
// "hideUnselected" optionally isolates the current geom during a
// geom/tree walk (hides everything else) so it reads clearly.
//
// The enabled flag and the walk preferences persist (viewing
// preferences); the per-walk index is transient and lives in the
// GalleryControls component (it depends on the current scope / scene).
export type GalleryWalk = "files" | "geoms" | "tree";
export type GeomWalkOrder = "scene" | "density";

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
        }),
        {name: "ada-gallery"},
    ),
);
