// Overlay lines and points (viewport/api/overlayLayer.ts) on the three.js viewport: wide lines
// (LineSegments2) and points in the scene, drawn by three.js directly and by the adacpp WebGPU
// viewport through its scene mirror (wide lines, depth-offset overlays, the overlay pass).
import * as THREE from "three";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";

import {requestRender} from "@/state/perfStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {colorsPerEndpoint, type OverlayLayer, type OverlayLines, type OverlayLook, type OverlayPoints} from "./overlayLayer";

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

function free(obj: THREE.Object3D): void {
    obj.removeFromParent();
    const drawn = obj as THREE.Mesh;
    drawn.geometry?.dispose();
    (drawn.material as THREE.Material | undefined)?.dispose();
}

/** An overlay layer whose objects are added to `parent()` (asked at each add: the scene on screen). */
export function createThreeOverlayLayer(parent: () => THREE.Object3D | null, name: string): OverlayLayer {
    const items = new Map<number, THREE.Object3D>();
    let nextHandle = 1;

    const put = (obj: THREE.Object3D, look: OverlayLook): number => {
        obj.name = name;
        if (look.matrix) {
            obj.matrixAutoUpdate = false;
            obj.matrix.fromArray(look.matrix);
        }
        obj.raycast = () => undefined; // never stands in front of the object a click is meant for
        parent()?.add(obj);
        if (look.clipped) clipWithModel(obj);
        const handle = nextHandle++;
        items.set(handle, obj);
        requestRender();
        return handle;
    };
    const drop = (handle: number): void => {
        const obj = items.get(handle);
        if (!obj) return;
        items.delete(handle);
        free(obj);
    };

    return {
        addLines: (spec) => put(makeLines(spec), spec),
        addPoints: (spec) => put(makePoints(spec), spec),
        setPositions: (handle, positions) => {
            const obj = items.get(handle);
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
