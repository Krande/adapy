// The single z-index registry.
//
// Before this, layering was decided per component with no shared reference: z-10 top
// bar, z-20 tree, z-30/z-40 mobile sheets, z-50 toasts, z-60 modal host, z-[70]
// shortcuts modal, and an inline z-index:1000 on the node editor. Adding a panel meant
// guessing a number bigger than whatever you happened to know about.
//
// Mirrored as --ada-z-* in src/ui/tokens.css so a plugin stylesheet and a core
// component read the same ordering. zIndex.test.ts asserts the two agree.

export const Z = {
    /** The 3D canvas. Everything else is above it. */
    canvas: 0,
    /** Canvas-anchored HUDs: colour legend, gizmo HUD, gallery controls. */
    overlayHud: 10,
    /** Docked regions. Above the canvas, but the canvas reflows rather than hiding. */
    dock: 20,
    /** Fly-outs from the collapsed tool rail. */
    railFlyout: 30,
    /** Floating (undocked) panels. */
    float: 40,
    /** Context menus, dropdowns, tooltips. */
    contextMenu: 50,
    /** Transient notifications. Above menus so a job failure is never hidden. */
    toast: 60,
    /** Modal dialogs and their backdrop. */
    dialog: 70,
    /** The ghost shown while dragging a panel. Above the dialog it may be dragged over. */
    dragPreview: 80,
    /** Dev-only overlays (stats panel, render profiler). */
    devOverlay: 90,
} as const;

export type ZLayer = keyof typeof Z;

/** Layers in ascending order — used by the ordering test and by the docs. */
export const Z_ORDER = Object.entries(Z)
    .sort((a, b) => a[1] - b[1])
    .map(([k]) => k) as ZLayer[];

/**
 * Inline style for a layer.
 *
 * Prefer this over a Tailwind `z-*` class: arbitrary z values were how the old
 * ad-hoc layering spread, and `zIndex.test.ts` scans for `z-[…]` classes outside this
 * module.
 */
export const zStyle = (layer: ZLayer): React.CSSProperties => ({zIndex: Z[layer]});
