/**
 * COG (centre of gravity) marker overlay.
 *
 * Owns: a small marker drawn at each SELECTED equipment's COG -- a sphere plus three short axis
 * lines, on top of (through) the box. Before this there was no way to see an equipment's COG at
 * all; no view toggle (it only ever shows for the current selection, so there is nothing to turn
 * off).
 *
 * COGx/COGy are offsets from the equipment's footprint CENTRE, COGz is from its BASE
 * (src/ada/topology/entities.py get_cog); the doc writer's own defaults are COGx=0, COGy=0,
 * COGz=size[2]/2 (documentSlice.ts). The offset is measured in the equipment's OWN (unrotated)
 * axes, then rotated by its ROT_X/Y/Z about the same pivot meshRebuild spins the box preview
 * about -- the footprint centre at the box's base -- so the marker tracks a rotated equipment
 * exactly as its compiled body would.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import type {CellBuilderScene} from "./sceneContext";
import {boxStructure, structureFrame} from "./structureFrames";

const SPHERE_COLOR = 0xffffff;
const AXIS_COLORS: readonly [number, number, number] = [0xff5555, 0x55ff55, 0x5599ff]; // X, Y, Z

/** A COG glyph sized off the equipment's own box: a translucent sphere "about 0.15 m across" for
 *  a roughly metre-sized box, scaled to the box's smallest dimension (clamped so a tiny or huge
 *  equipment still gets a sensible marker) plus three short axis lines through it. Drawn on top
 *  (depthTest off) so it reads through the box; never picked (its own `raycast` is a no-op, same
 *  as the ghost/snap-marker overlays). */
function makeCogMarker(size: readonly [number, number, number]): THREE.Group {
    const diameter = Math.min(0.3, Math.max(0.05, Math.min(size[0], size[1], size[2]) / 6));
    const radius = diameter / 2;

    const group = new THREE.Group();
    group.renderOrder = 8;
    group.raycast = () => {};

    const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(radius, 16, 12),
        new THREE.MeshBasicMaterial({color: SPHERE_COLOR, transparent: true, opacity: 0.85, depthTest: false, depthWrite: false}),
    );
    sphere.raycast = () => {};
    group.add(sphere);

    const axisLen = radius * 2.5;
    const positions = new Float32Array(18);
    const colors = new Float32Array(18);
    const axisVec: readonly [number, number, number][] = [
        [axisLen, 0, 0],
        [0, axisLen, 0],
        [0, 0, axisLen],
    ];
    for (let a = 0; a < 3; a++) {
        const [x, y, z] = axisVec[a];
        const c = AXIS_COLORS[a];
        const r = ((c >> 16) & 0xff) / 255;
        const g = ((c >> 8) & 0xff) / 255;
        const b = (c & 0xff) / 255;
        const base = a * 6;
        positions.set([-x, -y, -z, x, y, z], base);
        colors.set([r, g, b, r, g, b], base);
    }
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    lineGeo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const axes = new THREE.LineSegments(
        lineGeo,
        new THREE.LineBasicMaterial({vertexColors: true, transparent: true, opacity: 0.95, depthTest: false}),
    );
    axes.raycast = () => {};
    group.add(axes);

    return group;
}

function disposeCogMarker(group: THREE.Group): void {
    for (const child of group.children) {
        if (child instanceof THREE.Mesh || child instanceof THREE.LineSegments) {
            child.geometry.dispose();
            (child.material as THREE.Material).dispose();
        }
    }
}

export const clearCog = (ctx: CellBuilderScene): void => {
    // One frame per structure (./structureFrames.ts), like the ports overlay.
    for (const frame of [...ctx.cogGroup.children]) {
        for (const marker of [...frame.children]) {
            if (marker instanceof THREE.Group) disposeCogMarker(marker);
        }
        ctx.cogGroup.remove(frame);
    }
};

/** Redraw the COG marker(s) from the current selection. Only equipment is marked (a space/opening/
 *  loft has no COG); hidden when the builder itself is hidden, when nothing is selected, or for an
 *  equipment hidden individually (hiddenCellIds). */
export const rebuildCog = (ctx: CellBuilderScene): void => {
    clearCog(ctx);
    const st = useCellBuilderStore.getState();
    if (!st.active || !st.cellsVisible) {
        requestRender();
        return;
    }
    const selectedIds = new Set(st.selectedCellIds);
    if (st.selection?.kind === "cell") selectedIds.add(st.selection.cellId);
    for (const id of selectedIds) {
        const cell = st.cells[id];
        if (!cell || cell.kind !== "equipment" || st.hiddenCellIds.includes(id)) continue;
        const frame = structureFrame(ctx.cogGroup, boxStructure(st, {cell}), st.groups);
        const marker = makeCogMarker(cell.size);
        // Pivot: the footprint centre at the box's BASE -- the same point meshRebuild spins a
        // rotated equipment's box preview about -- so a rotated COG offset ends up exactly where
        // the compiled body's would.
        const pivot = new THREE.Vector3(cell.origin[0] + cell.size[0] / 2, cell.origin[1] + cell.size[1] / 2, cell.origin[2]);
        const cogx = typeof cell.params?.COGx === "number" ? cell.params.COGx : 0;
        const cogy = typeof cell.params?.COGy === "number" ? cell.params.COGy : 0;
        const cogz = typeof cell.params?.COGz === "number" ? cell.params.COGz : cell.size[2] / 2;
        const offset = new THREE.Vector3(cogx, cogy, cogz);
        const rot = cell.rotation;
        if (rot && (rot[0] || rot[1] || rot[2])) {
            offset.applyEuler(
                new THREE.Euler(
                    THREE.MathUtils.degToRad(rot[0]),
                    THREE.MathUtils.degToRad(rot[1]),
                    THREE.MathUtils.degToRad(rot[2]),
                    "ZYX",
                ),
            );
        }
        marker.position.copy(pivot).add(offset);
        marker.userData.__cellId = cell.id;
        frame.add(marker);
    }
    requestRender();
};
