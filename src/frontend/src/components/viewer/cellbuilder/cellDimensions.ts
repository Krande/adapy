/**
 * The selected cell's three dimensions while the RESIZE (S) gizmo is on.
 *
 * One dimension line per axis, along an edge of the box facing the camera and
 * set just outside it: dashed, an arrowhead at each end, the length in the middle
 * ("6.875m"), in the axis colour of the resize handles (X red, Y green, Z blue). A rotated
 * equipment box gets them in its own (rotated) frame. Rebuilt with the resize handles
 * (`syncGizmo` runs on every cell change, so the numbers follow a drag live, snapped), and
 * moved to the edges facing the camera as it orbits.
 *
 * The labels are screen-space sprites drawn over everything, so they stay readable at any
 * zoom and are never hidden behind the box.
 */

import * as THREE from "three";
import type {CellBox, Vec3} from "@/utils/cellbuilder/snap";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {HANDLE_AXIS_COLOR} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";

/** "6.875m": metres, at most three decimals, no trailing zeros. */
export function formatLength(metres: number): string {
    const rounded = Math.round(metres * 1000) / 1000;
    return `${Object.is(rounded, -0) ? 0 : rounded}m`;
}

export interface DimensionLine {
    axis: 0 | 1 | 2;
    from: Vec3;
    to: Vec3;
    length: number;
    /** Which side of the box the edge is on, per other axis (+1 / -1): the line's key. */
    sides: [number, number];
}

/** Per axis, the two axes across it. */
const OTHER_AXES = [[1, 2], [0, 2], [0, 1]] as const;

/**
 * Where the three dimension lines go for `cell` seen from `eye` (both in the cell's own
 * frame): per axis, the box edge parallel to it on the camera's side of the two other
 * axes, pushed out by `gap` along both so it sits just outside the box.
 */
export function dimensionLines(cell: CellBox, eye: Vec3, gap: number): DimensionLine[] {
    const out: DimensionLine[] = [];
    for (const axis of [0, 1, 2] as const) {
        const others = OTHER_AXES[axis];
        const from: Vec3 = [0, 0, 0];
        const to: Vec3 = [0, 0, 0];
        const sides: [number, number] = [1, 1];
        others.forEach((o, i) => {
            const centre = cell.origin[o] + cell.size[o] / 2;
            const side = eye[o] >= centre ? 1 : -1;
            sides[i] = side;
            const at = side > 0 ? cell.origin[o] + cell.size[o] + gap : cell.origin[o] - gap;
            from[o] = at;
            to[o] = at;
        });
        from[axis] = cell.origin[axis];
        to[axis] = cell.origin[axis] + cell.size[axis];
        out.push({axis, from, to, length: cell.size[axis], sides});
    }
    return out;
}

const LABEL_W = 256;
const LABEL_H = 64;
/** The label plate behind the length: the cell builder's readout colour. */
const LABEL_PLATE = "rgba(17,24,39,0.86)";
/** A label's sprite scale with `sizeAttenuation` off: about constant on screen, as the numeric readout. */
const LABEL_SCALE: readonly [number, number] = [0.12, 0.03];
/** Lines and arrowheads draw at the numeric readout's order, and the labels over them, so a
 *  number is never under a line. */
const LINE_RENDER_ORDER = 7;
const LABEL_RENDER_ORDER = 8;

function labelTexture(text: string, color: number): THREE.CanvasTexture {
    const canvas = document.createElement("canvas");
    canvas.width = LABEL_W;
    canvas.height = LABEL_H;
    const tex = new THREE.CanvasTexture(canvas);
    // The canvas is sRGB: said so, the dark plate and the axis colour come out as drawn.
    tex.colorSpace = THREE.SRGBColorSpace;
    const g = canvas.getContext("2d");
    // No 2D context (a canvas the browser refuses): a blank label beats no dimension line.
    if (!g) return tex;
    g.fillStyle = LABEL_PLATE;
    const r = 12;
    g.beginPath();
    g.moveTo(r, 2);
    g.arcTo(LABEL_W - 2, 2, LABEL_W - 2, LABEL_H - 2, r);
    g.arcTo(LABEL_W - 2, LABEL_H - 2, 2, LABEL_H - 2, r);
    g.arcTo(2, LABEL_H - 2, 2, 2, r);
    g.arcTo(2, 2, LABEL_W - 2, 2, r);
    g.fill();
    g.fillStyle = `#${color.toString(16).padStart(6, "0")}`;
    g.font = "bold 34px ui-monospace, monospace";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, LABEL_W / 2, LABEL_H / 2 + 2);
    tex.needsUpdate = true;
    return tex;
}

/** One drawn dimension line: the dashed shaft, its arrowheads and its label. */
interface DimensionPart {
    axis: 0 | 1 | 2;
    line: THREE.Line;
    heads: THREE.Mesh[];
    headLen: number;
    label: THREE.Sprite;
    /** Arrowheads at both ends (a dimension) or only at the far end (a move). */
    bothEnds: boolean;
}

/**
 * Add a dimension line along `axis` to `group`: dashed, arrowheaded, labelled `text`, in
 * the axis colour. `scale` (the box's largest size) sets the dash and arrowhead sizes.
 */
function addDimension(
    group: THREE.Group,
    axis: 0 | 1 | 2,
    length: number,
    text: string,
    scale: number,
    bothEnds: boolean,
): DimensionPart {
    const color = HANDLE_AXIS_COLOR[axis];
    const geom = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    const line = new THREE.Line(
        geom,
        new THREE.LineDashedMaterial({
            color,
            dashSize: Math.max(0.05, scale / 40),
            gapSize: Math.max(0.035, scale / 60),
            depthTest: false,
            transparent: true,
        }),
    );
    line.renderOrder = LINE_RENDER_ORDER;
    line.frustumCulled = false;
    // Arrowheads: cones pointing out along the axis, sized to the box (and to the line,
    // so a short one keeps a visible shaft).
    const headLen = Math.min(Math.max(0.12, scale / 25), Math.abs(length) * 0.3);
    const head = (): THREE.Mesh => {
        const cone = new THREE.Mesh(
            new THREE.ConeGeometry(headLen * 0.35, headLen, 12),
            new THREE.MeshBasicMaterial({color, depthTest: false, transparent: true}),
        );
        cone.renderOrder = LINE_RENDER_ORDER;
        return cone;
    };
    const heads = bothEnds ? [head(), head()] : [head()];
    const label = new THREE.Sprite(
        new THREE.SpriteMaterial({
            map: labelTexture(text, color),
            depthTest: false,
            transparent: true,
            sizeAttenuation: false,
            toneMapped: false,
        }),
    );
    label.scale.set(LABEL_SCALE[0], LABEL_SCALE[1], 1);
    label.renderOrder = LABEL_RENDER_ORDER;
    for (const o of [line, ...heads, label]) {
        o.userData.__excludeFromFit = true;
        group.add(o);
    }
    return {axis, line, heads, headLen, label, bothEnds};
}

/** Put a drawn dimension line from `from` to `to`, its arrowheads pointing out of the ends. */
function placeDimension(part: DimensionPart, from: Vec3, to: Vec3): void {
    const {axis, line, heads, headLen, label} = part;
    const pos = line.geometry.getAttribute("position");
    pos.setXYZ(0, ...from);
    pos.setXYZ(1, ...to);
    pos.needsUpdate = true;
    line.geometry.computeBoundingSphere();
    line.computeLineDistances();
    const a = new THREE.Vector3(...from);
    const b = new THREE.Vector3(...to);
    const dir = to[axis] >= from[axis] ? 1 : -1;
    const up = new THREE.Vector3(0, 1, 0);
    const along = new THREE.Vector3().setComponent(axis, (dir * headLen) / 2);
    const far = heads[heads.length - 1];
    far.position.copy(b).sub(along);
    far.quaternion.setFromUnitVectors(up, new THREE.Vector3().setComponent(axis, dir));
    if (part.bothEnds) {
        heads[0].position.copy(a).add(along);
        heads[0].quaternion.setFromUnitVectors(up, new THREE.Vector3().setComponent(axis, -dir));
    }
    label.position.copy(a).add(b).multiplyScalar(0.5);
}

/** Remove and free everything drawn in `group`. */
function disposeGroup(group: THREE.Group): void {
    for (let i = group.children.length - 1; i >= 0; i--) {
        const o = group.children[i];
        if (o instanceof THREE.Sprite) {
            // A sprite's geometry is three's shared quad: leave it.
            o.material.map?.dispose();
            o.material.dispose();
        } else if (o instanceof THREE.Mesh || o instanceof THREE.Line) {
            o.geometry.dispose();
            for (const m of Array.isArray(o.material) ? o.material : [o.material]) m.dispose();
        }
        group.remove(o);
    }
    group.visible = false;
}

/** Remove and free the resize dimension lines. */
export function disposeCellDimensions(ctx: CellBuilderScene): void {
    disposeGroup(ctx.dimensionGroup);
}

/** The camera position in `group`'s own frame (the cell's frame for a rotated box). */
const eyeIn = (group: THREE.Group): Vec3 | null => {
    const cam = getViewerRuntime().camera.current;
    if (!cam) return null;
    group.updateWorldMatrix(true, false);
    const p = group.worldToLocal(cam.getWorldPosition(new THREE.Vector3()));
    return [p.x, p.y, p.z];
};

/**
 * Show the dimension lines of `cell`, or none (null). `frame` places the box's own
 * (unrotated) frame in the scene: identity for an axis-aligned box, its rotation about the
 * footprint centre for a rotated equipment.
 */
export function syncCellDimensions(
    ctx: CellBuilderScene,
    cell: CellBox | null,
    gap: number,
    frame: THREE.Matrix4 = new THREE.Matrix4(),
): void {
    disposeCellDimensions(ctx);
    if (!cell) return;
    const group = ctx.dimensionGroup;
    group.matrixAutoUpdate = false;
    group.matrix.copy(frame);
    const eye = eyeIn(group);
    if (!eye) return;
    const maxSize = Math.max(...cell.size);
    const lines = dimensionLines(cell, eye, gap);
    const parts = lines.map((d) => addDimension(group, d.axis, d.length, formatLength(d.length), maxSize, true));
    const place = (ds: DimensionLine[]) => ds.forEach((d, i) => placeDimension(parts[i], d.from, d.to));
    place(lines);
    // Follow the camera round the box: the first line checks, before each frame, whether
    // another edge faces the camera now, and moves all three there (one more frame).
    let key = lines.map((d) => d.sides.join()).join("|");
    parts[0].line.onBeforeRender = () => {
        const now = eyeIn(group);
        if (!now) return;
        const next = dimensionLines(cell, now, gap);
        const nextKey = next.map((d) => d.sides.join()).join("|");
        if (nextKey === key) return;
        key = nextKey;
        place(next);
        requestRender();
    };
    group.visible = true;
}
