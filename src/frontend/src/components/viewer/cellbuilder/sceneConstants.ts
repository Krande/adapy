/**
 * Builder scene CONSTANTS.
 *
 * Owns: the palette the builder draws with (one colour per cell kind, hover and
 * selection tints), the opacities and pixel thresholds the interactions use, and
 * the default box extents an add mode falls back to when the catalog is
 * unreachable.
 */

import type {BuilderCell} from "@/state/cellBuilderStore";
import type {Vec3} from "@/utils/cellbuilder/snap";

/** Marks the gizmo HUD's delta field: Escape in it cancels a live axis-locked move (keyboard.ts). */
export const GIZMO_DELTA_FIELD_ATTR = "data-gizmo-delta";
export const CELL_COLOR = 0x3b82f6;
export const EQUIPMENT_COLOR = 0xf97316;
export const GHOST_COLOR = 0x22c55e;
export const HOVER_FACE_COLOR = 0xfacc15;
export const SELECTED_FACE_COLOR = 0xfb7185;
export const HOVER_EDGE_COLOR = 0xfacc15;
export const SELECTED_EDGE_COLOR = 0xfb7185;
export const HOVER_EDGE_WIDTH = 4; // px (fat lines — WebGL ignores LineBasicMaterial.linewidth)
export const SELECTED_EDGE_WIDTH = 6;
export const OPENING_COLOR = 0xef4444; // red — a negative-volume door/window cut
export const LOFT_COLOR = 0x14b8a6; // teal — a read-only swept-band (loft) proxy
export const EXCLUDED_FACE_COLOR = 0x64748b; // slate — a removed (excluded) loft panel
export const DEFAULT_EQUIPMENT_SIZE: Vec3 = [1, 1, 1];
// Last-resort box extents used ONLY when the cell/opening catalog is unreachable
// (the engine-advertised type otherwise supplies the size — see addModeSize).
export const FALLBACK_CELL_SIZE: Vec3 = [5, 5, 3];
export const FALLBACK_OPENING_SIZE: Vec3 = [1, 1, 2]; // door-ish; snaps to the wall it lands on

// The default box extent for the current add mode, taken from the selected
// engine-advertised cell/opening type (falling back to a sane constant only if
// the catalog couldn't be fetched). Equipment is sized from its own type/catalog
// at compile, so it keeps the unit default here.
export function addModeSize(st: {
    mode: string;
    cellTypes: {slug: string; size: [number, number, number]}[];
    selectedCellType: string | null;
    openingTypes: {slug: string; size: [number, number, number]}[];
    selectedOpeningType: string | null;
}): Vec3 {
    if (st.mode === "add-cell") {
        const t = st.cellTypes.find((x) => x.slug === st.selectedCellType);
        return t ? [t.size[0], t.size[1], t.size[2]] : FALLBACK_CELL_SIZE;
    }
    if (st.mode === "add-opening") {
        const t = st.openingTypes.find((x) => x.slug === st.selectedOpeningType);
        return t ? [t.size[0], t.size[1], t.size[2]] : FALLBACK_OPENING_SIZE;
    }
    return DEFAULT_EQUIPMENT_SIZE;
}

export const colorForKind = (kind: BuilderCell["kind"]): number =>
    kind === "cell"
        ? CELL_COLOR
        : kind === "opening"
          ? OPENING_COLOR
          : kind === "loft"
            ? LOFT_COLOR
            : EQUIPMENT_COLOR;
export const BASE_OPACITY = 0.3;
/** What the build leaves out (utils/cellbuilder/include), in the include view that dims it: its
 * faces at this share of their opacity, its edges dashed (m). */
export const EXCLUDED_DIM = 0.3;
export const EXCLUDED_DASH = 0.25;
export const EXCLUDED_GAP = 0.18;
export const LOFT_OPACITY = 0.35;
export const DRAG_START_PX = 4;
// Resize-handle sphere colour per axis (X red, Y green, Z blue).
export const HANDLE_AXIS_COLOR = [0xef4444, 0x22c55e, 0x3b82f6];
export const LONG_PRESS_MS = 500;
export const LONG_PRESS_MOVE_PX = 10;

// Pixel radius (screen space) within which the pointer "catches" a neighbour
// vertex. Screen-space so it behaves the same whether zoomed in or out — the
// old world-distance snap drifted and matched far-away corners on zoom-out.
export const SNAP_PX = 22;
