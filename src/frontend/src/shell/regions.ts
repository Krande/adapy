// Where a panel can live.
//
// Five regions, fixed. Panels declare a region, never a position — which is what makes
// swapping in a full drag-and-drop docking engine later a shell-level change rather
// than a 100-file change.
//
// Deliberately NOT a docking library. Two reasons, and the second is decisive:
//
//   1. vite.config.ts builds the pip-bundled desktop viewer as a single inlined HTML
//      (inlineDynamicImports), so a ~90 KB dock library lands whole in that one file.
//   2. Every docking library re-parents DOM nodes when you drag a tab. ThreeCanvas
//      appends the WebGL canvas imperatively (ThreeCanvas.tsx:101) and its removeChild
//      cleanup is commented out (:261) — a re-parent would orphan the GL context.
//
// What we need is four fixed docks with splitters and tab groups, which is a few
// hundred lines and no risk to the canvas.

export const DOCK_IDS = ["left", "right", "bottom", "float", "overlay"] as const;
export type DockId = (typeof DOCK_IDS)[number];

/** Docks that occupy a grid track and push the viewport, rather than covering it. */
export const DOCKED_IDS = ["left", "right", "bottom"] as const;
export type DockedId = (typeof DOCKED_IDS)[number];

export const isDocked = (id: DockId): id is DockedId => (DOCKED_IDS as readonly string[]).includes(id);

/** Size limits per docked region, in px. Clamped on every resize and on rehydrate. */
export const DOCK_LIMITS: Record<DockedId, {min: number; max: number; default: number}> = {
    // Wide enough for a deep tree path plus a type icon without truncating to nothing.
    left: {min: 180, max: 560, default: 260},
    // Properties rows are label-left/value-right; below ~200px they wrap and become
    // unreadable.
    right: {min: 200, max: 640, default: 300},
    // The FEA data table lives here — wide and short is the point.
    bottom: {min: 120, max: 600, default: 220},
};

export const DOCK_LABEL: Record<DockId, string> = {
    left: "Left dock",
    right: "Right dock",
    bottom: "Bottom dock",
    float: "Floating panels",
    overlay: "Viewport overlay",
};
