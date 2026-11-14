import {useCellBuilderStore} from "@/state/cellBuilderStore";

// ``?grid=<step>&snap=<distance>``: the cell builder's grid step and snap distance, in model
// units, set from a link - for a tool that opens its model with the module it is built on
// (a stiffener spacing, say) rather than the builder's 0.1 default.
//
// Read once by useUrlParamLoad and left in the address bar (unlike ?file=): a reload of the
// same link keeps the model's grid, and nothing here names a scope or a file.

export const GRID_PARAM = "grid";
export const SNAP_PARAM = "snap";

/** A positive, finite number, else null. */
function positive(raw: string | null): number | null {
    if (raw === null || raw.trim() === "") return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
}

/** Apply ``?grid=`` / ``?snap=`` from ``search``; returns what was applied. */
export function applyViewParams(search: string): {grid: number | null; snap: number | null} {
    const params = new URLSearchParams(search);
    const grid = positive(params.get(GRID_PARAM));
    const snap = positive(params.get(SNAP_PARAM));
    const s = useCellBuilderStore.getState();
    if (grid !== null) s.setGridStep(grid);
    if (snap !== null) s.setSnapThreshold(snap);
    return {grid, snap};
}
