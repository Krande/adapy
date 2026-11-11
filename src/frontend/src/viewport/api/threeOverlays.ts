// Overlay lines, points and shapes (viewport/api/overlayLayer.ts) on the three.js viewport: wide lines
// (LineSegments2), points and unlit meshes in the scene, drawn by three.js directly and by the adacpp WebGPU
// viewport through its scene mirror (wide lines, depth-offset overlays, the overlay pass).
import * as THREE from "three";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";

import {requestRender} from "@/state/perfStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {colorsPerEndpoint, grownCapacity, type OverlayLayer, type OverlayLines, type OverlayLook, type OverlayMesh, type OverlayPoints} from "./overlayLayer";

// Lines drawn over the model stack in the order they were added.
let onTopSeq = 0;

function makeLines(spec: OverlayLines): LineSegments2 {
    const segments = spec.segments.length / 6;
    const geom = new LineSegmentsGeometry();
    // A copy: the geometry keeps the array it is given.
    geom.setPositions(new Float32Array(spec.segments));
    if (spec.colors) geom.setColors(colorsPerEndpoint(spec.colors, new Float32Array(segments * 6)));
    const opacity = spec.opacity ?? 1;
    const material = new LineMaterial({
        vertexColors: !!spec.colors,
        linewidth: spec.widthPx ?? 2,
        dashed: !!spec.dashed,
        dashSize: spec.dashed?.dash ?? 1,
        gapSize: spec.dashed?.gap ?? 1,
        opacity,
        transparent: opacity < 1 || !!spec.onTop,
        depthTest: !spec.onTop,
        depthWrite: false,
        // In front of the faces they run along (a stiffener on its plate), not hidden in them.
        polygonOffset: true,
        polygonOffsetFactor: -2,
        polygonOffsetUnits: -2,
    });
    if (!spec.colors) {
        const [r, g, b] = spec.color ?? [1, 1, 1];
        material.color.setRGB(r, g, b);
    }
    const lines = new LineSegments2(geom, material);
    if (spec.dashed) lines.computeLineDistances();
    lines.renderOrder = 7;
    if (spec.onTop) {
        lines.renderOrder = 20 + (onTopSeq++ % 100000) * 1e-5;
        // The WebGPU viewport draws these with three.js over its image (its sprite pass): nothing of
        // the model can then hide them, which is what they ask for.
        lines.userData.__overlayPass = true;
    }
    // LineMaterial turns linewidth into pixels from the viewport size: CSS pixels, as `widthPx`.
    lines.onBeforeRender = (renderer) => {
        const el = renderer.domElement as HTMLCanvasElement | undefined;
        if (el?.clientWidth) material.resolution.set(el.clientWidth, el.clientHeight);
        else renderer.getSize(material.resolution);
    };
    return lines;
}

function makePoints(spec: OverlayPoints): THREE.Points {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(spec.positions), 3));
    if (spec.colors) geom.setAttribute("color", new THREE.BufferAttribute(new Float32Array(spec.colors), 3));
    const opacity = spec.opacity ?? 1;
    const material = new THREE.PointsMaterial({
        vertexColors: !!spec.colors,
        size: spec.sizePx ?? 6,
        sizeAttenuation: false,
        opacity,
        transparent: opacity < 1 || !!spec.onTop,
        depthTest: !spec.onTop,
        depthWrite: false,
    });
    if (!spec.colors) {
        const [r, g, b] = spec.color ?? [1, 1, 1];
        material.color.setRGB(r, g, b);
    }
    const points = new THREE.Points(geom, material);
    points.renderOrder = spec.onTop ? 20 + (onTopSeq++ % 100000) * 1e-5 : 7;
    if (spec.onTop) points.userData.__overlayPass = true;
    return points;
}

function makeMesh(spec: OverlayMesh): THREE.Mesh {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(spec.positions), 3));
    if (spec.colors) geom.setAttribute("color", new THREE.BufferAttribute(new Float32Array(spec.colors), 3));
    geom.setIndex(new THREE.BufferAttribute(new Uint32Array(spec.indices), 1));
    const opacity = spec.opacity ?? 1;
    const material = new THREE.MeshBasicMaterial({
        vertexColors: !!spec.colors,
        side: THREE.DoubleSide,
        opacity,
        transparent: opacity < 1 || !!spec.onTop,
        depthTest: !spec.onTop,
        depthWrite: false,
    });
    if (!spec.colors) {
        const [r, g, b] = spec.color ?? [1, 1, 1];
        material.color.setRGB(r, g, b);
    }
    const mesh = new THREE.Mesh(geom, material);
    mesh.renderOrder = spec.onTop ? 20 + (onTopSeq++ % 100000) * 1e-5 : 7;
    if (spec.onTop) mesh.userData.__overlayPass = true;
    return mesh;
}

function free(obj: THREE.Object3D): void {
    obj.removeFromParent();
    const drawn = obj as THREE.Mesh;
    drawn.geometry?.dispose();
    (drawn.material as THREE.Material | undefined)?.dispose();
}

/** `count` numbers of `from` into `to`, and the rest of `to` filled with the last `stride` of them:
 *  what is held beyond what is drawn then lies on a point that is drawn, so the box around all of
 *  it is the box around what is seen. Nothing is allocated. */
function fill(to: Float32Array, from: ArrayLike<number>, count: number, stride: number): void {
    for (let i = 0; i < count; i++) to[i] = from[i];
    if (count < stride) return void to.fill(0, count);
    for (let i = count; i < to.length; i++) to[i] = to[i - stride];
}

/** What a handle draws, and what `setGeometry` and `setLook` need to know of how it was made. */
interface Item {
    obj: THREE.Object3D;
    kind: "lines" | "points" | "mesh";
    /** It has a colour per segment or point. */
    colored: boolean;
    dashed: boolean;
    onTop: boolean;
}

/** New line geometry with room for `capacity` segments. */
function lineGeometry(capacity: number, colored: boolean): LineSegmentsGeometry {
    const geom = new LineSegmentsGeometry();
    geom.setPositions(new Float32Array(Math.max(1, capacity) * 6));
    if (colored) geom.setColors(new Float32Array(Math.max(1, capacity) * 6));
    return geom;
}

function replaceLines(item: Item, positions: Float32Array, count: number, colors: Float32Array | null): void {
    const lines = item.obj as LineSegments2;
    let geom = lines.geometry;
    let start = geom.attributes.instanceStart as THREE.InterleavedBufferAttribute;
    const capacity = (start.data.array as Float32Array).length / 6;
    // Dashes are measured along a geometry as it was made: made again, with exactly what is drawn.
    if (count > capacity || item.dashed) {
        geom.dispose();
        geom = lines.geometry = lineGeometry(item.dashed ? count : grownCapacity(capacity, count), item.colored);
        start = geom.attributes.instanceStart as THREE.InterleavedBufferAttribute;
    }
    fill(start.data.array as Float32Array, positions, count * 6, 3);
    start.data.needsUpdate = true;
    if (item.colored && colors) {
        const tint = geom.attributes.instanceColorStart as THREE.InterleavedBufferAttribute;
        const held = tint.data.array as Float32Array;
        for (let s = 0, o = 0; s < count * 3; s += 3, o += 6) {
            held[o] = held[o + 3] = colors[s];
            held[o + 1] = held[o + 4] = colors[s + 1];
            held[o + 2] = held[o + 5] = colors[s + 2];
        }
        tint.data.needsUpdate = true;
    }
    geom.instanceCount = count;
    // Measured again when something asks (a fit); never per frame.
    geom.boundingBox = null;
    geom.boundingSphere = null;
    if (item.dashed && count > 0) lines.computeLineDistances();
}

function replacePoints(item: Item, positions: Float32Array, count: number, colors: Float32Array | null): void {
    const points = item.obj as THREE.Points;
    let geom = points.geometry;
    let attr = geom.getAttribute("position") as THREE.BufferAttribute;
    if (count > attr.count) {
        // A larger attribute on the same geometry would leave the old buffer on the GPU: a new
        // geometry, and the old one disposed with its buffers.
        const capacity = grownCapacity(attr.count, count);
        geom.dispose();
        geom = points.geometry = new THREE.BufferGeometry();
        attr = new THREE.BufferAttribute(new Float32Array(capacity * 3), 3);
        geom.setAttribute("position", attr);
        if (item.colored) geom.setAttribute("color", new THREE.BufferAttribute(new Float32Array(capacity * 3), 3));
    }
    fill(attr.array as Float32Array, positions, count * 3, 3);
    attr.needsUpdate = true;
    if (item.colored && colors) {
        const tint = geom.getAttribute("color") as THREE.BufferAttribute;
        const held = tint.array as Float32Array;
        for (let i = 0; i < count * 3; i++) held[i] = colors[i];
        tint.needsUpdate = true;
    }
    geom.setDrawRange(0, count);
    geom.boundingBox = null;
    geom.boundingSphere = null;
}

/** An overlay layer whose objects are added to `parent()` (asked at each add: the scene on screen). */
export function createThreeOverlayLayer(parent: () => THREE.Object3D | null, name: string): OverlayLayer {
    const items = new Map<number, Item>();
    let nextHandle = 1;

    const put = (obj: THREE.Object3D, look: OverlayLook, kind: Item["kind"], colored: boolean, dashed = false): number => {
        obj.name = name;
        if (look.matrix) {
            obj.matrixAutoUpdate = false;
            obj.matrix.fromArray(look.matrix);
        }
        obj.raycast = () => undefined; // never stands in front of the object a click is meant for
        if (look.fitted === false) obj.userData.__excludeFromFit = true;
        parent()?.add(obj);
        if (look.clipped) clipWithModel(obj);
        const handle = nextHandle++;
        items.set(handle, {obj, kind, colored, dashed, onTop: !!look.onTop});
        requestRender();
        return handle;
    };
    const drop = (handle: number): void => {
        const item = items.get(handle);
        if (!item) return;
        items.delete(handle);
        free(item.obj);
    };

    return {
        addLines: (spec) => put(makeLines(spec), spec, "lines", !!spec.colors, !!spec.dashed),
        addPoints: (spec) => put(makePoints(spec), spec, "points", !!spec.colors),
        addMesh: (spec) => put(makeMesh(spec), spec, "mesh", !!spec.colors),
        setPositions: (handle, positions) => {
            const obj = items.get(handle)?.obj;
            if (!obj) return;
            if (obj instanceof LineSegments2) {
                // In place: the instance buffer keeps its identity, and its version tells both renderers.
                const start = obj.geometry.attributes.instanceStart as THREE.InterleavedBufferAttribute;
                const held = start.data.array as Float32Array;
                held.set(positions.length === held.length ? positions : positions.subarray(0, held.length));
                start.data.needsUpdate = true;
                obj.geometry.computeBoundingSphere();
                if ((obj.material as LineMaterial).dashed) obj.computeLineDistances();
            } else {
                const attr = (obj as THREE.Points).geometry.getAttribute("position") as THREE.BufferAttribute;
                const held = attr.array as Float32Array;
                held.set(positions.length === held.length ? positions : positions.subarray(0, held.length));
                attr.needsUpdate = true;
                (obj as THREE.Points).geometry.computeBoundingSphere();
            }
            requestRender();
        },
        setGeometry: (handle, positions, count, colors) => {
            const item = items.get(handle);
            if (!item || item.kind === "mesh") return;
            const stride = item.kind === "lines" ? 6 : 3;
            const n = Math.max(0, Math.min(count ?? Infinity, Math.floor(positions.length / stride)));
            // What changes every frame is not measured every frame to be culled: it is always drawn.
            item.obj.frustumCulled = false;
            if (item.kind === "lines") replaceLines(item, positions, n, colors ?? null);
            else replacePoints(item, positions, n, colors ?? null);
            requestRender();
        },
        setLook: (handle, look) => {
            const item = items.get(handle);
            if (!item) return;
            const {obj} = item;
            const material = (obj as THREE.Mesh).material as THREE.Material & {color: THREE.Color};
            if (look.visible !== undefined) obj.visible = look.visible;
            if (look.opacity !== undefined) {
                material.opacity = Math.max(0, Math.min(1, look.opacity));
                const transparent = material.opacity < 1 || item.onTop;
                if (material.transparent !== transparent) {
                    material.transparent = transparent;
                    material.needsUpdate = true;
                }
            }
            if (look.color && !item.colored) material.color.setRGB(look.color[0], look.color[1], look.color[2]);
            if (look.matrix !== undefined) {
                obj.matrixAutoUpdate = false;
                if (look.matrix) obj.matrix.fromArray(look.matrix);
                else obj.matrix.identity();
                obj.matrixWorldNeedsUpdate = true;
            }
            requestRender();
        },
        remove: (handle) => {
            drop(handle);
            requestRender();
        },
        clear: () => {
            for (const handle of [...items.keys()]) drop(handle);
            requestRender();
        },
        dispose: () => {
            for (const handle of [...items.keys()]) drop(handle);
            requestRender();
        },
    };
}
