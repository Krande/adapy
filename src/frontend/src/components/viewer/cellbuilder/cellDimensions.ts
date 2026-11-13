/**
 * The selected cell's three dimensions while the RESIZE (S) gizmo is on.
 *
 * One dimension line per axis, along an edge of the box facing the camera and set just
 * outside it: dashed, an arrowhead at each end, the length in the middle ("6.875m"), in
 * the axis colour of the resize handles (X red, Y green, Z blue). The labels are
 * screen-space sprites drawn over everything, so they stay readable at any zoom and are
 * never hidden behind the box. Rebuilt with the resize handles (`syncGizmo` runs on every
 * cell change, so the numbers follow a drag live, snapped), and moved to the edges facing
 * the camera as it orbits.
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

/**
 * Where the three dimension lines go for `cell` seen from `eye` (both in the cell's own
 * frame): per axis, the box edge parallel to it on the camera's side of the two other
 * axes, pushed out by `gap` along both so it sits just outside the box.
 */
export function dimensionLines(cell: CellBox, eye: Vec3, gap: number): DimensionLine[] {
    const out: DimensionLine[] = [];
    for (const axis of [0, 1, 2] as const) {
        const others = [0, 1, 2].filter((a) => a !== axis) as [number, number];
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

function labelTexture(text: string, color: number): THREE.CanvasTexture {
    const canvas = document.createElement("canvas");
    canvas.width = LABEL_W;
    canvas.height = LABEL_H;
    const g = canvas.getContext("2d")!;
    g.fillStyle = "rgba(17,24,39,0.86)";
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
    const tex = new THREE.CanvasTexture(canvas);
    // The canvas is sRGB: said so, the dark plate and the axis colour come out as drawn.
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.needsUpdate = true;
    return tex;
}

/** Remove and free the dimension lines. */
export function disposeCellDimensions(ctx: CellBuilderScene): void {
    const group = ctx.dimensionGroup;
    for (let i = group.children.length - 1; i >= 0; i--) {
        const o = group.children[i] as THREE.Mesh | THREE.Line | THREE.Sprite;
        // A sprite's geometry is three's shared quad: leave it.
        if (!(o instanceof THREE.Sprite)) o.geometry.dispose();
        const m = o.material as THREE.SpriteMaterial | THREE.Material;
        if ("map" in m && m.map) m.map.dispose();
        m.dispose();
        group.remove(o);
    }
    group.visible = false;
}

const eyeIn = (ctx: CellBuilderScene): Vec3 | null => {
    const cam = getViewerRuntime().camera.current;
    if (!cam) return null;
    const p = ctx.dimensionGroup.parent
        ? ctx.dimensionGroup.parent.worldToLocal(cam.getWorldPosition(new THREE.Vector3()))
        : cam.getWorldPosition(new THREE.Vector3());
    return [p.x, p.y, p.z];
};

/** Show the dimension lines of `cell`, or none (null). */
export function syncCellDimensions(ctx: CellBuilderScene, cell: CellBox | null, gap: number): void {
    disposeCellDimensions(ctx);
    if (!cell) return;
    const eye = eyeIn(ctx);
    if (!eye) return;
    const group = ctx.dimensionGroup;
    const maxSize = Math.max(...cell.size);
    const lines = dimensionLines(cell, eye, gap);
    const parts: {line: THREE.Line; heads: [THREE.Mesh, THREE.Mesh]; label: THREE.Sprite}[] = [];
    for (const d of lines) {
        const color = HANDLE_AXIS_COLOR[d.axis];
        const geom = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...d.from), new THREE.Vector3(...d.to)]);
        const line = new THREE.Line(
            geom,
            new THREE.LineDashedMaterial({
                color,
                dashSize: Math.max(0.05, maxSize / 40),
                gapSize: Math.max(0.035, maxSize / 60),
                depthTest: false,
                transparent: true,
            }),
        );
        line.computeLineDistances();
        line.renderOrder = 7;
        line.frustumCulled = false;
        // Arrowheads: cones pointing out along the axis, sized to the box (and to the line,
        // so a thin cell keeps a visible shaft).
        const headLen = Math.min(Math.max(0.12, maxSize / 25), d.length * 0.3);
        const head = (outward: number): THREE.Mesh => {
            const cone = new THREE.Mesh(
                new THREE.ConeGeometry(headLen * 0.35, headLen, 12),
                new THREE.MeshBasicMaterial({color, depthTest: false, transparent: true}),
            );
            cone.renderOrder = 7;
            cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3().setComponent(d.axis, outward));
            return cone;
        };
        const heads: [THREE.Mesh, THREE.Mesh] = [head(-1), head(1)];
        const label = new THREE.Sprite(
            new THREE.SpriteMaterial({
                map: labelTexture(formatLength(d.length), color),
                depthTest: false,
                transparent: true,
                sizeAttenuation: false,
                toneMapped: false,
            }),
        );
        label.scale.set(0.12, 0.03, 1); // ~constant screen size, like the numeric readout
        label.renderOrder = 8;
        for (const o of [line, ...heads, label]) {
            o.userData.__excludeFromFit = true;
            group.add(o);
        }
        parts.push({line, heads, label});
    }
    const place = (ds: DimensionLine[]) => {
        ds.forEach((d, i) => {
            const {line, heads, label} = parts[i];
            const pos = line.geometry.getAttribute("position") as THREE.BufferAttribute;
            pos.setXYZ(0, ...d.from);
            pos.setXYZ(1, ...d.to);
            pos.needsUpdate = true;
            line.computeLineDistances();
            const headLen = (heads[0].geometry as THREE.ConeGeometry).parameters.height;
            const a = new THREE.Vector3(...d.from);
            const b = new THREE.Vector3(...d.to);
            const along = new THREE.Vector3().setComponent(d.axis, headLen / 2);
            heads[0].position.copy(a).add(along);
            heads[1].position.copy(b).sub(along);
            label.position.copy(a).add(b).multiplyScalar(0.5);
        });
    };
    place(lines);
    // Follow the camera round the box: the first line checks, before each frame, whether
    // another edge faces the camera now, and moves all three there (one more frame).
    let key = lines.map((d) => d.sides.join()).join("|");
    parts[0].line.onBeforeRender = () => {
        const now = eyeIn(ctx);
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
