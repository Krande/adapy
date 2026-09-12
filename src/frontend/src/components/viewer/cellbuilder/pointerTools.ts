/**
 * POINTER handling: pick, hover, ghost placement and face dragging.
 *
 * Owns: the raycast pick against the builder meshes, cell/face/edge hover, the
 * magnetic placement ghost in the add modes, grid-quantised face drags, tap vs.
 * orbit resolution, long-press and right-click context menus. Every DOM pointer
 * event the builder listens to resolves here.
 */

import * as THREE from "three";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {BOX_FACE_SIDES, applyFaceOffset, edgeHitOnFace, quantize, snapBox, type CellBox, type EdgeHit, type Vec3} from "@/utils/cellbuilder/snap";
import {DRAG_START_PX, GHOST_COLOR, LONG_PRESS_MOVE_PX, LONG_PRESS_MS, OPENING_COLOR, addModeSize} from "./sceneConstants";
import {offsetVec, worldToModel, type CellBuilderScene} from "./sceneContext";
import {lineParamFromRay} from "./geometryHelpers";
import {refreshEdgeOverlays, refreshFaceStyles} from "./overlays";
import {applyCellTranslate, cellCenterModel, computeMove, endModalMove, equipContainedIn, showSnapMarker} from "./cellGizmo";
import {pickPort} from "./portGizmo";

export const startFaceDrag = (ctx: CellBuilderScene, cell: BuilderCell, faceIndex: number, ev: PointerEvent, immediate: boolean): boolean => {
    // Equipment is sized by its type — never face-drag-resized in the scene.
    if (cell.kind !== "cell") return false;
    const side = BOX_FACE_SIDES[faceIndex];
    if (!side) return false;
    const center = new THREE.Vector3(
        cell.origin[0] + cell.size[0] / 2,
        cell.origin[1] + cell.size[1] / 2,
        cell.origin[2] + cell.size[2] / 2,
    ).add(offsetVec(ctx));
    const lineDir = new THREE.Vector3(side.axis === 0 ? 1 : 0, side.axis === 1 ? 1 : 0, side.axis === 2 ? 1 : 0);
    const startT = lineParamFromRay(ctx.raycaster.ray, center, lineDir);
    if (startT === null) return false;
    ctx.drag = {
        cellId: cell.id,
        faceIndex,
        axis: side.axis,
        positiveFace: side.positive,
        startBox: {origin: [...cell.origin], size: [...cell.size]},
        lineOrigin: center,
        lineDir,
        startT,
        startClientX: ev.clientX,
        startClientY: ev.clientY,
        started: false,
        pointerId: ev.pointerId,
    };
    if (immediate) {
        const st = useCellBuilderStore.getState();
        ctx.drag.started = true;
        st.setMode("drag-face");
        st.beginTransaction();
        const runtimeCtl = getViewerRuntime().controls.current;
        if (runtimeCtl) runtimeCtl.enabled = false;
        ctx.renderer.domElement.setPointerCapture(ev.pointerId);
    }
    return true;
};

export const clearLongPress = (ctx: CellBuilderScene) => {
    if (ctx.longPressTimer !== null) {
        clearTimeout(ctx.longPressTimer);
        ctx.longPressTimer = null;
    }
};

export const armLongPress = (ctx: CellBuilderScene, ev: PointerEvent) => {
    clearLongPress(ctx);
    if (ev.pointerType !== "touch") return;
    const st = useCellBuilderStore.getState();
    if (!st.active) return;
    // A press on the translate/rotate gizmo's handle is a drag, not a long-press.
    if ((st.gizmoMode === "translate" || st.gizmoMode === "rotate") && ctx.gizmo.axis) return;
    ctx.longPressStartX = ev.clientX;
    ctx.longPressStartY = ev.clientY;
    const {clientX, clientY} = ev;
    // A long-press ON A PORT ARROW opens the port Move/Rotate menu (the touch
    // equivalent of the desktop right-click, which orbit-controls otherwise
    // swallow as a camera drag). Ports win over the cell body behind them.
    const hitPort = pickPort(ctx);
    if (hitPort) {
        ctx.longPressTimer = setTimeout(() => {
            ctx.longPressTimer = null;
            ctx.drag = null;
            ctx.pendingSelect = null;
            useCellBuilderStore
                .getState()
                .openPortMenu(clientX, clientY, hitPort.cellId, hitPort.portName);
        }, LONG_PRESS_MS);
        return;
    }
    const hit = pickBuilderMesh(ctx);
    if (!hit) return;
    const cellId = hit.object.userData.__cellId as string;
    ctx.longPressTimer = setTimeout(() => {
        ctx.longPressTimer = null;
        // A long-press wins over a pending face-drag/selection.
        ctx.drag = null;
        ctx.pendingSelect = null;
        useCellBuilderStore.getState().openContextMenu(clientX, clientY, cellId);
    }, LONG_PRESS_MS);
};

export const setPointer = (ctx: CellBuilderScene, ev: PointerEvent) => {
    const rect = ctx.renderer.domElement.getBoundingClientRect();
    ctx.pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    ctx.pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    ctx.raycaster.setFromCamera(ctx.pointer, getViewerRuntime().camera.current ?? (ctx.camera as any));
};

export const pickBuilderMesh = (ctx: CellBuilderScene): THREE.Intersection | null => {
    if (!ctx.cellsGroup.visible) return null; // hidden cells aren't pickable
    // Per-cell hidden boxes are excluded so a click passes through to
    // whatever geometry (e.g. the compiled result) sits underneath.
    // intersectObjects targets the meshes directly, bypassing the group, so
    // it ignores mesh.visible — filter explicitly.
    const meshes = [...ctx.meshById.values()].filter((m) => m.visible);
    const hits = ctx.raycaster.intersectObjects(meshes, false);
    return hits.length ? hits[0] : null;
};

export const syncCursor = (ctx: CellBuilderScene) => {
    ctx.renderer.domElement.style.cursor = ctx.hoveredEdge ? "crosshair" : ctx.hovered ? "pointer" : "";
};

export const setHoveredFace = (ctx: CellBuilderScene, mesh: THREE.Mesh | null, faceIndex: number) => {
    const same = ctx.hovered?.mesh === mesh && ctx.hovered?.faceIndex === faceIndex;
    if (same || (!ctx.hovered && !mesh)) return;
    ctx.hovered = mesh ? {mesh, faceIndex} : null;
    syncCursor(ctx);
    refreshFaceStyles(ctx);
};

export const sameEdge = (ctx: CellBuilderScene, a: EdgeHit | null | undefined, b: EdgeHit | null | undefined): boolean =>
    !!a && !!b && a.axis === b.axis && a.boundaryAxis === b.boundaryAxis && a.boundaryPositive === b.boundaryPositive;

export const setHoveredEdge = (ctx: CellBuilderScene, next: {cellId: string; faceIndex: number; edge: EdgeHit} | null) => {
    const same =
        (next === null && ctx.hoveredEdge === null) ||
        (next !== null &&
            ctx.hoveredEdge !== null &&
            next.cellId === ctx.hoveredEdge.cellId &&
            next.faceIndex === ctx.hoveredEdge.faceIndex &&
            sameEdge(ctx, next.edge, ctx.hoveredEdge.edge));
    if (same) return;
    ctx.hoveredEdge = next;
    syncCursor(ctx);
    refreshEdgeOverlays(ctx);
    requestRender();
};

export const updateGhost = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const size = addModeSize(st);
    // Place on top of a hovered cell, else on the model's ground plane.
    const hit = pickBuilderMesh(ctx);
    let base: Vec3 | null = null;
    // Ground level (model Z) for empty-space placement: the existing cells'
    // lowest floor, NOT model z=0. A project template is centred far from the
    // origin (e.g. greenvolt sits ~498 m up, translated back to the middle of
    // the scene), so a z=0 plane would be way below the visible model and the
    // new cell would land off-screen — which reads as "+ Cell does nothing".
    const existing = Object.values(st.cells);
    const groundZ = existing.length ? Math.min(...existing.map((c) => c.origin[2])) : 0;
    let z = groundZ;
    if (hit) {
        const cellId = hit.object.userData.__cellId as string;
        const cell = st.cells[cellId];
        base = worldToModel(ctx, hit.point);
        z = cell ? cell.origin[2] + cell.size[2] : base[2];
    } else {
        // Plane at world z = groundZ + offset (i.e. model z = groundZ).
        const planeWorldZ = groundZ + offsetVec(ctx).z;
        const groundPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -planeWorldZ);
        const w = ctx.raycaster.ray.intersectPlane(groundPlane, new THREE.Vector3());
        if (w) base = worldToModel(ctx, w);
    }
    if (!base) {
        ctx.ghost.visible = false;
        ctx.ghostBox = null;
        return;
    }
    let box: CellBox = {
        origin: [
            quantize(base[0] - size[0] / 2, st.gridStep),
            quantize(base[1] - size[1] / 2, st.gridStep),
            quantize(z, st.gridStep),
        ],
        size,
    };
    box = snapBox(box, Object.values(st.cells), st.snapThreshold);
    ctx.ghostBox = box;
    // Red for an opening (a cut), green for an additive cell/equipment.
    (ctx.ghost.material as THREE.MeshBasicMaterial).color.setHex(
        st.mode === "add-opening" ? OPENING_COLOR : GHOST_COLOR,
    );
    ctx.ghost.scale.set(...box.size);
    ctx.ghost.position.set(
        box.origin[0] + box.size[0] / 2,
        box.origin[1] + box.size[1] / 2,
        box.origin[2] + box.size[2] / 2,
    );
    ctx.ghost.visible = true;
    requestRender();
};

export const onPointerDown = (ctx: CellBuilderScene, ev: PointerEvent) => {
    const st = useCellBuilderStore.getState();
    if (!st.active || ev.button !== 0) return;
    setPointer(ctx, ev);

    // Axis-locked modal move active: a left-click confirms the placement and
    // drops the axis lock (back to the 3-axis gizmo). No handle grab needed.
    if (ctx.modalMove) {
        endModalMove(ctx, false);
        st.setGizmoAxisLock(null);
        ev.stopPropagation();
        return;
    }

    if (
        st.mode === "add-cell" ||
        st.mode === "add-equipment" ||
        st.mode === "add-opening"
    ) {
        if (ctx.placeEntry) {
            // Numeric placement is driving the ghost — a click doesn't place.
            ev.stopPropagation();
            return;
        }
        updateGhost(ctx);
        if (ctx.ghostBox) {
            const kind =
                st.mode === "add-cell"
                    ? "cell"
                    : st.mode === "add-opening"
                      ? "opening"
                      : "equipment";
            st.addCell(kind, ctx.ghostBox.origin, ctx.ghostBox.size);
        }
        ev.stopPropagation();
        return;
    }

    // Touch long-press → cell context menu (armed here, fires on hold).
    armLongPress(ctx, ev);

    // A gizmo owns the cell: manipulate its handles, and a tap on empty
    // space exits the gizmo (a drag on empty space still orbits).
    if (st.gizmoMode !== "none") {
        if (st.gizmoMode === "resize") {
            const hits = ctx.raycaster.intersectObjects(ctx.resizeGroup.children, false);
            if (hits.length) {
                const fi = hits[0].object.userData.__resizeFace as number;
                const cellId = hits[0].object.userData.__cellId as string;
                const cell = st.cells[cellId];
                if (cell && startFaceDrag(ctx, cell, fi, ev, true)) {
                    clearLongPress(ctx);
                    ev.stopPropagation();
                }
                return;
            }
        }
        // Translate/rotate: TransformControls owns the pointer over its handles.
        if ((st.gizmoMode === "translate" || st.gizmoMode === "rotate") && ctx.gizmo.axis) return;
        // Missed the handles: over a cell body do nothing (let it orbit);
        // over empty space, arm an exit resolved as a tap on pointerup.
        if (!pickBuilderMesh(ctx)) {
            ctx.pendingGizmoExit = {x: ev.clientX, y: ev.clientY};
        }
        return;
    }

    // "none" select mode = pure navigation: don't grab faces for
    // select/drag, let the camera controls handle the pointer.
    if (st.selectMode === "none") return;

    const hit = pickBuilderMesh(ctx);
    if (!hit || !hit.face) return;
    const cellId = hit.object.userData.__cellId as string;
    const cell = st.cells[cellId];
    if (!cell) return;

    // Face-drag resizing is opt-in. When it's OFF, never grab or stop the
    // pointer here: let OrbitControls own it so a drag anywhere — including
    // over a cell — orbits the camera. A no-travel tap still selects, via
    // pendingSelect resolved on pointerup.
    if (!st.faceDragResize) {
        ctx.pendingSelect = {cellId, faceIndex: hit.face.materialIndex, x: ev.clientX, y: ev.clientY};
        return;
    }

    // Resize enabled: begin a pending face-drag that becomes a real drag
    // after DRAG_START_PX of travel, or a selection click on a bare
    // pointerup. Stop propagation so the drag owns the pointer, not orbit.
    if (!startFaceDrag(ctx, cell, hit.face.materialIndex, ev, false)) return;
    ev.stopPropagation();
};

export const resolveClickSelection = (ctx: CellBuilderScene, target: {cellId: string; faceIndex: number}, ev: PointerEvent) => {
    const st = useCellBuilderStore.getState();
    // "none" mode: a plain click selects nothing (free navigation).
    if (st.selectMode === "none") return;
    const cell = st.cells[target.cellId];
    if (!cell) return;

    setPointer(ctx, ev);
    const hit = pickBuilderMesh(ctx);

    // Explicit selection: the panel's select-mode fully decides what a
    // click picks — no implicit border-proximity edge override. setSelection
    // surfaces the details in the Selected Object Info panel.
    if (st.selectMode === "edge") {
        // Nearest border edge of the clicked face (Infinity tolerance =
        // always resolve to the closest of the face's four borders).
        const edge = hit
            ? edgeHitOnFace(cell, target.faceIndex, worldToModel(ctx, hit.point), Infinity)
            : null;
        if (edge) {
            st.setSelection({kind: "edge", cellId: cell.id, faceIndex: target.faceIndex, edge});
        }
        return;
    }
    if (st.selectMode === "face") {
        st.setSelection({kind: "face", cellId: cell.id, faceIndex: target.faceIndex});
        return;
    }
    // Whole-cell pick: add-mode toggles the cell in the multi-select set (so
    // several cells can be copied/hidden at once); otherwise it's a single
    // selection.
    if (st.cellAddMode) {
        st.toggleCellSelection(cell.id);
    } else {
        st.setSelection({kind: "cell", cellId: cell.id});
    }
};

export const onPointerMove = (ctx: CellBuilderScene, ev: PointerEvent) => {
    const st = useCellBuilderStore.getState();
    if (!st.active) return;

    // A moving finger cancels a pending long-press (it's a drag, not a hold).
    if (ctx.longPressTimer !== null &&
        Math.hypot(ev.clientX - ctx.longPressStartX, ev.clientY - ctx.longPressStartY) > LONG_PRESS_MOVE_PX) {
        clearLongPress(ctx);
    }
    // A drag on empty space is an orbit, not a gizmo-exit tap.
    if (ctx.pendingGizmoExit &&
        Math.hypot(ev.clientX - ctx.pendingGizmoExit.x, ev.clientY - ctx.pendingGizmoExit.y) > DRAG_START_PX) {
        ctx.pendingGizmoExit = null;
    }
    // A drag over a cell is an orbit, not a selection tap.
    if (ctx.pendingSelect &&
        Math.hypot(ev.clientX - ctx.pendingSelect.x, ev.clientY - ctx.pendingSelect.y) > DRAG_START_PX) {
        ctx.pendingSelect = null;
    }

    setPointer(ctx, ev);

    // Axis-locked modal move: the cell tracks the pointer along the locked
    // axis, no button held. First move seeds the reference (no jump) and opens
    // one undo step; later moves slide the cell (with vertex snapping).
    if (ctx.modalMove && st.gizmoMode === "translate" && st.gizmoAxisLock === ctx.modalMove.axis) {
        const cell = st.cells[ctx.modalMove.cellId];
        if (!cell) {
            endModalMove(ctx, false);
            return;
        }
        const t = lineParamFromRay(ctx.raycaster.ray, ctx.modalMove.lineOrigin, ctx.modalMove.lineDir);
        if (t !== null) {
            if (ctx.modalMove.startT === null) {
                ctx.modalMove.startT = t;
                st.beginTransaction();
                // Capture the equipment riding along with this cell.
                ctx.translateEquip = equipContainedIn(ctx, cell);
            } else {
                const axis = ctx.modalMove.axis;
                const center = cellCenterModel(ctx, cell);
                const startCenterAxis = ctx.modalMove.startBox.origin[axis] + ctx.modalMove.startBox.size[axis] / 2;
                center[axis] = startCenterAxis + (t - ctx.modalMove.startT);
                const {origin, target} = computeMove(ctx, cell, center, axis);
                applyCellTranslate(ctx, cell, origin);
                showSnapMarker(ctx, target);
            }
        }
        ev.stopPropagation();
        return;
    }

    if (ctx.drag) {
        if (!ctx.drag.started) {
            const dx = ev.clientX - ctx.drag.startClientX;
            const dy = ev.clientY - ctx.drag.startClientY;
            if (Math.hypot(dx, dy) < DRAG_START_PX) return;
            // Face-drag resizing is opt-in: without it, dragging a face does
            // nothing (resizing goes through the explicit resize gizmo). Drop
            // the pending drag so it's neither a resize nor a stray select.
            if (!st.faceDragResize) {
                ctx.drag = null;
                return;
            }
            ctx.drag.started = true;
            st.setMode("drag-face");
            // Coalesce the whole drag into one undo step.
            st.beginTransaction();
            const runtimeCtl = getViewerRuntime().controls.current;
            if (runtimeCtl) runtimeCtl.enabled = false;
            ctx.renderer.domElement.setPointerCapture(ctx.drag.pointerId);
        }
        const t = lineParamFromRay(ctx.raycaster.ray, ctx.drag.lineOrigin, ctx.drag.lineDir);
        if (t === null) return;
        // signed face displacement along +axis; applyFaceOffset knows which
        // face moves (positive face scales size, negative face shifts origin)
        const offset = quantize(t - ctx.drag.startT, st.gridStep);
        const next = applyFaceOffset(ctx.drag.startBox, ctx.drag.axis, ctx.drag.positiveFace, offset, st.gridStep || 0.1);
        st.updateCell(ctx.drag.cellId, {origin: next.origin, size: next.size});
        ev.stopPropagation();
        return;
    }

    if (
        st.mode === "add-cell" ||
        st.mode === "add-equipment" ||
        st.mode === "add-opening"
    ) {
        if (!ctx.placeEntry) updateGhost(ctx); // numeric placement owns the ghost when active
        return;
    }

    if (st.mode === "idle") {
        // Explicit selection only: hovering never auto-highlights a
        // face/edge (that yellow hover pick read as an accidental
        // selection). The chosen element highlights on an explicit click;
        // hover just offers a cursor hint over a pickable cell.
        setHoveredEdge(ctx, null);
        setHoveredFace(ctx, null, -1);
        const overCell =
            st.selectMode !== "none" &&
            st.gizmoMode === "none" &&
            pickBuilderMesh(ctx) !== null;
        ctx.renderer.domElement.style.cursor =
            overCell ? (st.selectMode === "edge" ? "crosshair" : "pointer") : "";
    }
};

// End a (pending or active) face-drag. Always restores the camera controls
// and releases the pointer capture the drag grabbed — critically also on
// ``pointercancel``, which touch devices fire routinely when the browser's
// gesture recogniser takes over (scroll/pinch) or a second finger lands. If
// only ``pointerup`` restored them, a cancelled touch would leave
// ``controls.enabled = false`` forever and the camera would appear broken.
export const finalizeDrag = (ctx: CellBuilderScene, ev: PointerEvent, cancelled: boolean) => {
    if (!ctx.drag) return;
    const wasDrag = ctx.drag.started;
    const pending = ctx.drag;
    ctx.drag = null;
    if (wasDrag) {
        const st = useCellBuilderStore.getState();
        st.setMode("idle");
        st.endTransaction(); // close the coalesced-drag undo step
        const runtimeCtl = getViewerRuntime().controls.current;
        if (runtimeCtl) runtimeCtl.enabled = true;
        try {
            ctx.renderer.domElement.releasePointerCapture(pending.pointerId);
        } catch {
            /* already released */
        }
    } else if (!cancelled) {
        // A pending-but-never-dragged pointerup is a selection click; a
        // cancelled gesture selects nothing.
        resolveClickSelection(ctx, pending, ev);
    }
    ev.stopPropagation();
};

export const onPointerUp = (ctx: CellBuilderScene, ev: PointerEvent) => {
    clearLongPress(ctx);
    // A tap (no travel) on empty space exits the active gizmo.
    if (ctx.pendingGizmoExit) {
        const moved = Math.hypot(ev.clientX - ctx.pendingGizmoExit.x, ev.clientY - ctx.pendingGizmoExit.y);
        ctx.pendingGizmoExit = null;
        if (moved < DRAG_START_PX) useCellBuilderStore.getState().setGizmoMode("none");
    }
    // A no-travel tap on a cell (face-drag resize off) resolves to a
    // selection. We never stopped propagation, so OrbitControls still
    // handled the pointer — a drag would have cleared pendingSelect above.
    if (ctx.pendingSelect) {
        const moved = Math.hypot(ev.clientX - ctx.pendingSelect.x, ev.clientY - ctx.pendingSelect.y);
        const target = ctx.pendingSelect;
        ctx.pendingSelect = null;
        if (moved < DRAG_START_PX) resolveClickSelection(ctx, target, ev);
    }
    finalizeDrag(ctx, ev, false);
};
export const onPointerCancel = (ctx: CellBuilderScene, ev: PointerEvent) => {
    clearLongPress(ctx);
    ctx.pendingGizmoExit = null;
    ctx.pendingSelect = null;
    finalizeDrag(ctx, ev, true);
};

// Desktop: right-click over a cell opens the same context menu.
export const onContextMenu = (ctx: CellBuilderScene, ev: MouseEvent) => {
    const st = useCellBuilderStore.getState();
    if (!st.active) return;
    setPointer(ctx, ev as unknown as PointerEvent);
    // A right-click ON a port arrow opens the port edit menu (Move / Rotate)
    // — checked before the cell menu so a port glyph over a cell wins.
    const port = pickPort(ctx);
    if (port) {
        ev.preventDefault();
        st.openPortMenu(ev.clientX, ev.clientY, port.cellId, port.portName);
        return;
    }
    const hit = pickBuilderMesh(ctx);
    if (!hit) return;
    ev.preventDefault();
    const cellId = hit.object.userData.__cellId as string;
    st.openContextMenu(ev.clientX, ev.clientY, cellId);
};
