import {hideSelectedRanges, unhideAllRanges} from "@/utils/scene/visibility";
import {centerViewOnSelection} from "@/utils/scene/centerViewOnSelection";
import {frameCells} from "@/utils/scene/frameCells";
import {zoomToAll} from "@/components/viewer/sceneHelpers/setupCameraControlsHandlers";
import {cameraRef, controlsRef, sceneRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";

// Viewport actions for the tool rail.
//
// These DELEGATE to the handlers the keyboard shortcuts and the classic Properties panel
// already call — hideSelectedRanges, unhideAllRanges, centerViewOnSelection, zoomToAll,
// frameCells. Nothing here reimplements behaviour; the rail is a second entry point to
// the same code, so the two can never diverge.
//
// The cell-vs-mesh dispatch mirrors ObjectInfoBoxComponent's: builder cells are excluded
// from the fittable scene, so they are framed explicitly, and "unhide all" always clears
// BOTH systems so "show everything" cannot leave something hidden in the other one.

/** Hide the current selection. Cells when a builder selection is active, else ranges. */
export function hideSelection(): void {
    const cb = useCellBuilderStore.getState();
    if (cb.active !== null && cb.selection !== null) {
        cb.hideCells(cb.selectedCellIds);
    } else {
        hideSelectedRanges();
    }
    requestRender();
}

/** Reveal everything, in both the range and the cell systems. */
export function unhideAll(): void {
    unhideAllRanges();
    const cb = useCellBuilderStore.getState();
    if (cb.active !== null) cb.unhideAllCells();
    requestRender();
}

/** Frame the whole scene. */
export function fitAll(): void {
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;

    const cb = useCellBuilderStore.getState();
    if (cb.active !== null && Object.keys(cb.cells).length > 0 && frameCells("all", controls, camera)) {
        requestRender();
        return;
    }
    if (scene) {
        zoomToAll(scene, camera, controls);
        requestRender();
    }
}

/** Centre the view on the current selection. */
export function focusSelection(): void {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;

    const cb = useCellBuilderStore.getState();
    if (cb.active !== null && cb.selection !== null && frameCells(cb.selectedCellIds, controls, camera)) {
        requestRender();
        return;
    }
    centerViewOnSelection(controls, camera);
    requestRender();
}
