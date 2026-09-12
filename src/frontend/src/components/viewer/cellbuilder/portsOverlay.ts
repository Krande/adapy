/**
 * PORT / NOZZLE overlay.
 *
 * Owns: the coloured arrows and nozzle markers drawn at each placed equipment's
 * I/O positions, plus the site terminals a system run can end at. Arrows sit on
 * layer 1 so they never intercept a normal pick — only the port raycaster
 * sees them.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {portsForEquipment} from "@/utils/cellbuilder/ports";
import {hexToInt, portColorInt, uniquePortColorHexByIndex} from "@/utils/portColor";
import type {CellBuilderScene} from "./sceneContext";

export const disposeArrow = (ctx: CellBuilderScene, a: THREE.ArrowHelper) => {
    a.line.geometry.dispose();
    (a.line.material as THREE.Material).dispose();
    a.cone.geometry.dispose();
    (a.cone.material as THREE.Material).dispose();
};

export const clearPorts = (ctx: CellBuilderScene) => {
    for (let i = ctx.portsGroup.children.length - 1; i >= 0; i--) {
        const o = ctx.portsGroup.children[i];
        if (o instanceof THREE.ArrowHelper) disposeArrow(ctx, o);
        // Nozzle markers share portMarkerGeom (freed at cleanup) but own
        // their material.
        else if (o instanceof THREE.Mesh) (o.material as THREE.Material).dispose();
        ctx.portsGroup.remove(o);
    }
};

// Redraw the port overlay from the placed equipment cells. Each port becomes
// a coloured arrow at (cell centre in x/y, cell base in z) + local position,
// matching the compiler's equipment origin (X+LX/2, Y+LY/2, Z) and the
// catalog preview's arrow colours. Arrows sit on layer 1 so they never
// intercept picks.
export const rebuildPorts = (ctx: CellBuilderScene) => {
    clearPorts(ctx);
    const st = useCellBuilderStore.getState();
    ctx.portsGroup.visible = st.portsOverlayVisible;
    if (!st.active || !st.portsOverlayVisible) {
        requestRender();
        return;
    }
    for (const cell of Object.values(st.cells)) {
        const ports = portsForEquipment(cell, st.equipmentTypes);
        if (!ports.length) continue;
        const len = Math.max(0.2, 0.3 * Math.max(cell.size[0], cell.size[1], cell.size[2]));
        const cx = cell.origin[0] + cell.size[0] / 2;
        const cy = cell.origin[1] + cell.size[1] / 2;
        const cz = cell.origin[2];
        // Ports are local to the footprint centre; spin them with the same
        // ZYX rotation the compiler applies so the overlay tracks the placed
        // (rotated) nozzles.
        const rot = cell.rotation;
        const portEuler =
            cell.kind === "equipment" && rot && (rot[0] || rot[1] || rot[2])
                ? new THREE.Euler(
                      THREE.MathUtils.degToRad(rot[0]),
                      THREE.MathUtils.degToRad(rot[1]),
                      THREE.MathUtils.degToRad(rot[2]),
                      "ZYX",
                  )
                : null;
        for (let pi = 0; pi < ports.length; pi++) {
            const p = ports[pi];
            const pos = p.position ?? [0, 0, 0];
            const dv = p.direction_vector ?? [0, 0, 1];
            const localPos = new THREE.Vector3(pos[0], pos[1], pos[2]);
            if (portEuler) localPos.applyEuler(portEuler);
            // The nozzle position: where the port physically attaches.
            const nozzle = new THREE.Vector3(cx + localPos.x, cy + localPos.y, cz + localPos.z);
            // Colour by the port's index in the list → every I/O is unique.
            const color = portColorInt(p, pi);
            // direction_vector is the outward nozzle normal; the arrow shows
            // actual flow — INPUT points into the equipment, OUTPUT points
            // out, INOUT stays outward.
            const dir = new THREE.Vector3(dv[0], dv[1], dv[2]);
            if (portEuler) dir.applyEuler(portEuler);
            if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
            dir.normalize();
            if (p.direction === "IN") dir.negate();
            // Keep the whole arrow OUTSIDE the equipment box so it stays
            // visible, and always land a marker at the nozzle position.
            // Outward flow (OUT/INOUT): tail at the nozzle, tip points out.
            // Inward flow (IN): offset the tail outward by `len` so the TIP
            // lands exactly on the nozzle position and the shaft sits
            // outside the box rather than disappearing inside it.
            const tail =
                p.direction === "IN"
                    ? nozzle.clone().addScaledVector(dir, -len)
                    : nozzle;
            const arrow = new THREE.ArrowHelper(dir, tail, len, color, len * 0.4, len * 0.25);
            // Tag every part of the arrow (+ its marker) with the port
            // identity so a right-click can resolve which equipment port it
            // hit and open the port edit menu. Layer 1 keeps them out of the
            // normal (layer-0) cell/face pick.
            arrow.traverse((o) => {
                o.layers.set(1);
                o.userData.__portCellId = cell.id;
                o.userData.__portName = p.name;
            });
            ctx.portsGroup.add(arrow);
            // Nozzle-position marker: a small sphere at the attachment point
            // so the position is shown independently of the arrow tip.
            const marker = new THREE.Mesh(
                ctx.portMarkerGeom,
                new THREE.MeshBasicMaterial({color}),
            );
            marker.position.copy(nozzle);
            marker.scale.setScalar(len * 0.08);
            marker.layers.set(1);
            marker.userData.__portCellId = cell.id;
            marker.userData.__portName = p.name;
            ctx.portsGroup.add(marker);
        }
    }
    // Site I/O terminals: a system connection can terminate at a model-boundary
    // site input/output (not an equipment port). Draw it like a port — an arrow
    // at its world position along its orientation — so the boundary interfaces
    // show up in the same overlay. Their positions are already world-space
    // (unlike equipment ports, which are cell-relative).
    const siteLen = 0.6;
    let siteIdx = 0;
    for (const sys of Object.values(st.systems)) {
        for (const conn of sys.connections) {
            if (!conn.site) continue;
            const pos = conn.position ?? [0, 0, 0];
            const dv = conn.directionVector ?? [0, 0, 1];
            const nozzle = new THREE.Vector3(pos[0], pos[1], pos[2]);
            const color = hexToInt(uniquePortColorHexByIndex(siteIdx++));
            // directionVector points into the model (the run's departure). An
            // input flows in along it; an output flows off-model, so its arrow
            // points the other way.
            const dir = new THREE.Vector3(dv[0], dv[1], dv[2]);
            if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
            dir.normalize();
            if (conn.direction === "OUT") dir.negate();
            const tail =
                conn.direction === "OUT" ? nozzle.clone().addScaledVector(dir, -siteLen) : nozzle;
            const arrow = new THREE.ArrowHelper(dir, tail, siteLen, color, siteLen * 0.4, siteLen * 0.25);
            arrow.traverse((o) => o.layers.set(1));
            ctx.portsGroup.add(arrow);
            const marker = new THREE.Mesh(ctx.portMarkerGeom, new THREE.MeshBasicMaterial({color}));
            marker.position.copy(nozzle);
            marker.scale.setScalar(siteLen * 0.1);
            marker.layers.set(1);
            ctx.portsGroup.add(marker);
        }
    }
    requestRender();
};

// --- Direct-manipulation gizmos -------------------------------------
// Translate: a THREE TransformControls widget drives a proxy whose
// model-space position maps back to the selected cell's centre. The proxy
// lives in the container so it shares the model offset. Resize: six
// touch-friendly spheres at the cell's face centres, each dragged with the
// same applyFaceOffset math as a face drag.
