/**
 * Equipment-PORT edit gizmo.
 *
 * Owns: a TransformControls dedicated to one equipment port — translate moves
 * the nozzle (snapping to the equipment bbox corners and any loaded CAD
 * vertices), rotate spins the outward direction about the port anchor. Separate
 * from the cell gizmo so the two can never clobber each other, and its own
 * layer-1 raycaster so a right-click can resolve which port was hit.
 */

import * as THREE from "three";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {portSnapTargets, portsForEquipment} from "@/utils/cellbuilder/ports";
import type {Vec3} from "@/utils/cellbuilder/snap";
import {SNAP_PX} from "./sceneConstants";
import {offsetVec, type CellBuilderScene} from "./sceneContext";
import {collectCadVerts, displayBoxForCell} from "./cadPreview";
import {showSnapMarker} from "./cellGizmo";


// Resolve an equipment port's anchor (nozzle) + outward direction in MODEL
// space, mirroring rebuildPorts' math (including the equipment's ZYX spin).
export const portGeom = (
    ctx: CellBuilderScene,
    cellId: string,
    portName: string,
): {anchor: Vec3; dir: Vec3; center: Vec3; quat: THREE.Quaternion | null} | null => {
    const st = useCellBuilderStore.getState();
    const cell = st.cells[cellId];
    if (!cell || cell.kind !== "equipment") return null;
    const p = portsForEquipment(cell, st.equipmentTypes).find((x) => x.name === portName);
    if (!p) return null;
    const cx = cell.origin[0] + cell.size[0] / 2;
    const cy = cell.origin[1] + cell.size[1] / 2;
    const cz = cell.origin[2];
    const rot = cell.rotation;
    const quat =
        rot && (rot[0] || rot[1] || rot[2])
            ? new THREE.Quaternion().setFromEuler(
                  new THREE.Euler(
                      THREE.MathUtils.degToRad(rot[0]),
                      THREE.MathUtils.degToRad(rot[1]),
                      THREE.MathUtils.degToRad(rot[2]),
                      "ZYX",
                  ),
              )
            : null;
    const pos = p.position ?? [0, 0, 0];
    const lp = new THREE.Vector3(pos[0], pos[1], pos[2]);
    if (quat) lp.applyQuaternion(quat);
    const dv = p.direction_vector ?? [0, 0, 1];
    const d = new THREE.Vector3(dv[0], dv[1], dv[2]);
    if (quat) d.applyQuaternion(quat);
    if (d.lengthSq() < 1e-9) d.set(0, 0, 1);
    d.normalize();
    return {
        anchor: [cx + lp.x, cy + lp.y, cz + lp.z],
        dir: [d.x, d.y, d.z],
        center: [cx, cy, cz],
        quat,
    };
};

// CAD-mesh vertices (model space) for a CAD-backed equipment, so a port can
// snap onto the real geometry — not just the bounding box. Best-effort:
// active only when "Use CAD models" is on and a loaded mesh in the scene is
// named for this equipment; downsampled so a dense asset can't stall the
// per-move snap search. Boxes-only topology view yields none (bbox corners
// remain the snap set).
// The loaded scene mesh whose name matches this equipment cell (the CAD the
// compiler spliced in), or null. The compiler emits one mesh per equipment
// named for the cell (see collectCadVerts / _cad_transform).
export const nearestPortSnapToPointer = (ctx: CellBuilderScene, cell: BuilderCell): Vec3 | null => {
    const cam = getViewerRuntime().camera.current ?? (ctx.camera as THREE.PerspectiveCamera);
    const off = offsetVec(ctx);
    const rect = ctx.renderer.domElement.getBoundingClientRect();
    const sx0 = (ctx.pointer.x * 0.5 + 0.5) * rect.width;
    const sy0 = (-ctx.pointer.y * 0.5 + 0.5) * rect.height;
    const v = new THREE.Vector3();
    let best: Vec3 | null = null;
    let bestD = SNAP_PX;
    for (const t of portSnapTargets(displayBoxForCell(ctx, cell).box, collectCadVerts(ctx, cell))) {
        v.set(t[0] + off.x, t[1] + off.y, t[2] + off.z).project(cam);
        if (v.z < -1 || v.z > 1) continue;
        const sx = (v.x * 0.5 + 0.5) * rect.width;
        const sy = (-v.y * 0.5 + 0.5) * rect.height;
        const d = Math.hypot(sx - sx0, sy - sy0);
        if (d <= bestD) {
            bestD = d;
            best = t;
        }
    }
    return best;
};

// A dedicated raycaster restricted to layer 1 (where the port glyphs live),
// so a right-click can hit a port arrow/marker that the normal pick ignores.

export const pickPort = (ctx: CellBuilderScene): {cellId: string; portName: string} | null => {
    const st = useCellBuilderStore.getState();
    if (!st.active || !st.portsOverlayVisible) return null;
    ctx.portRaycaster.setFromCamera(ctx.pointer, getViewerRuntime().camera.current ?? (ctx.camera as THREE.Camera));
    const hits = ctx.portRaycaster.intersectObjects(ctx.portsGroup.children, true);
    for (const h of hits) {
        let o: THREE.Object3D | null = h.object;
        while (o) {
            const cid = o.userData.__portCellId as string | undefined;
            const pn = o.userData.__portName as string | undefined;
            if (cid && pn) return {cellId: cid, portName: pn};
            o = o.parent;
        }
    }
    return null;
};

export const syncPortGizmo = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const runtimeCam = getViewerRuntime().camera.current;
    if (runtimeCam) ctx.portGizmo.camera = runtimeCam;
    const pg = st.portGizmo;
    const info = pg ? portGeom(ctx, pg.cellId, pg.portName) : null;
    const on = !!(st.active && pg && info && st.portsOverlayVisible && st.cellsVisible);
    if (on && pg && info) {
        if (!ctx.portGizmo.dragging) {
            ctx.portProxy.rotation.set(0, 0, 0);
            ctx.portProxy.position.set(info.anchor[0], info.anchor[1], info.anchor[2]);
        }
        if (ctx.portGizmo.object !== ctx.portProxy) ctx.portGizmo.attach(ctx.portProxy);
        if (pg.mode === "rotate") {
            ctx.portGizmo.setMode("rotate");
            ctx.portGizmo.setRotationSnap(THREE.MathUtils.degToRad(15));
        } else {
            ctx.portGizmo.setMode("translate");
            ctx.portGizmo.setTranslationSnap(
                st.gizmoVertexSnap ? null : st.gridStep > 0 ? st.gridStep : null,
            );
        }
        ctx.portGizmoHelper.visible = true;
    } else {
        if (ctx.portGizmo.object) ctx.portGizmo.detach();
        ctx.portGizmoHelper.visible = false;
    }
    requestRender();
};
/** Wire the port gizmo's drag events (nozzle move / direction spin). */
export function installPortGizmo(ctx: CellBuilderScene): void {

    ctx.portGizmo.addEventListener("dragging-changed", (e: any) => {
        const st = useCellBuilderStore.getState();
        const runtimeCtl = getViewerRuntime().controls.current;
        if (runtimeCtl) runtimeCtl.enabled = !e.value;
        if (e.value) {
            st.beginTransaction();
            const pg = st.portGizmo;
            const info = pg ? portGeom(ctx, pg.cellId, pg.portName) : null;
            ctx.portRotateStartDir = info ? new THREE.Vector3(info.dir[0], info.dir[1], info.dir[2]) : null;
            ctx.portProxy.rotation.set(0, 0, 0); // rotate delta accumulates from identity
        } else {
            st.endTransaction();
            ctx.portRotateStartDir = null;
            showSnapMarker(ctx, null);
        }
        requestRender();
    });

    ctx.portGizmo.addEventListener("objectChange", () => {
        const st = useCellBuilderStore.getState();
        const pg = st.portGizmo;
        if (!pg) return;
        const cell = st.cells[pg.cellId];
        const info = portGeom(ctx, pg.cellId, pg.portName);
        if (!cell || !info) return;
        const invQuat = info.quat ? info.quat.clone().invert() : null;
        if (pg.mode === "translate") {
            // Proxy position is the dragged (model-space) nozzle; vertex snap
            // pulls it onto the nearest bbox corner / CAD vertex under the cursor.
            let nozzle: Vec3 = [ctx.portProxy.position.x, ctx.portProxy.position.y, ctx.portProxy.position.z];
            let target: Vec3 | null = null;
            if (st.gizmoVertexSnap) {
                target = nearestPortSnapToPointer(ctx, cell);
                if (target) nozzle = target;
            }
            // Back out the equipment's footprint centre + spin to the port's
            // stored LOCAL position (so it round-trips like the type geometry).
            const local = new THREE.Vector3(
                nozzle[0] - info.center[0],
                nozzle[1] - info.center[1],
                nozzle[2] - info.center[2],
            );
            if (invQuat) local.applyQuaternion(invQuat);
            st.updateEquipmentPort(pg.cellId, pg.portName, {position: [local.x, local.y, local.z]});
            showSnapMarker(ctx, target);
        } else {
            // Rotate about the anchor: apply the proxy's accumulated rotation to
            // the start direction, then back out the equipment spin to store the
            // port's LOCAL outward direction. Position is unchanged.
            if (!ctx.portRotateStartDir) return;
            const world = ctx.portRotateStartDir.clone().applyQuaternion(ctx.portProxy.quaternion).normalize();
            if (invQuat) world.applyQuaternion(invQuat);
            world.normalize();
            st.updateEquipmentPort(pg.cellId, pg.portName, {
                direction_vector: [world.x, world.y, world.z],
            });
        }
    });

}
