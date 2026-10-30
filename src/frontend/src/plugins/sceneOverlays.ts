// Renderer-neutral scene overlays for plugins: lines, labels and element colours.
//
// A plugin describes WHAT to draw in plain data; core builds the scene objects, attaches them where they
// move and clip with the model, and both renderers draw them -- three.js directly, the adacpp WebGPU
// viewport through its scene mirror (wide lines, depth-offset overlays, the sprite pass). A plugin that
// built its own three.js objects instead depended on how one renderer draws them, and on the FEA mesh's
// internals (its draw-range ids, index layout and morph attributes).
//
// Coordinates are the FEA model's own (the active FEA mesh's local frame, which core recentres), or world
// coordinates when no FEA model is loaded. Colours are sRGB, as CSS: "#ffaa00" or [r, g, b] in 0..1.

import * as THREE from "three";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";

import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getActiveFeaMesh} from "@/utils/scene/handlers/load_fea_streaming";
import {clipWithModel} from "@/utils/scene/section_clipping";
import type {
    SceneColor,
    SceneElementPaint,
    SceneLabel,
    SceneLabelOptions,
    SceneLines,
    SceneOverlay,
} from "./registry";

/** The FEA mesh's structural surface used here (core's CustomBatchedMesh). */
interface FeaMesh extends THREE.Mesh {
    drawRanges?: Map<string, [number, number]>;
    hideBatchDrawRange?: (ids: Iterable<string>) => void;
    isolateDrawRanges?: (keep: ReadonlySet<string>) => void;
    unhideAllDrawRanges?: () => void;
    getHiddenRanges?: () => ReadonlySet<string>;
}

const feaMesh = () => getActiveFeaMesh() as FeaMesh | null;
const rangeId = (element: number) => `E${element}`;

const owned = new Map<string, Set<THREE.Object3D>>();

function linear(c: SceneColor, out = new THREE.Color()): THREE.Color {
    return typeof c === "string" ? out.set(c) : out.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace);
}

/** Put `obj` where the plugin's coordinates mean what it says, and keep it in step with the model. */
function attach(owner: string, obj: THREE.Object3D): SceneOverlay {
    const mesh = feaMesh();
    const parent: THREE.Object3D | null = mesh ?? getViewerRuntime().scene.current;
    obj.userData.__pluginOwner = owner;
    obj.traverse((o) => {
        o.raycast = () => undefined; // never stands in front of the object a click is meant for
        if (mesh) o.layers.mask = mesh.layers.mask;
    });
    parent?.add(obj);
    clipWithModel(obj);
    let set = owned.get(owner);
    if (!set) owned.set(owner, (set = new Set()));
    set.add(obj);
    requestRender();
    return {remove: () => dispose(owner, obj)};
}

function dispose(owner: string, obj: THREE.Object3D): void {
    obj.removeFromParent();
    obj.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
        for (const mat of mats) {
            (mat as THREE.SpriteMaterial).map?.dispose();
            mat.dispose();
        }
    });
    owned.get(owner)?.delete(obj);
    requestRender();
}

/** Remove every overlay `owner` added through these verbs (SceneHandle.remove(owner) calls this). */
export function removeOwnerOverlays(owner: string): void {
    for (const obj of [...(owned.get(owner) ?? [])]) dispose(owner, obj);
}

export function addLines(owner: string, spec: SceneLines): SceneOverlay {
    const positions = Float32Array.from(spec.segments);
    const segments = positions.length / 6;
    if (!Number.isInteger(segments)) throw new Error(`addLines: segments needs 6 numbers per segment, got ${positions.length}`);
    if (spec.colors && spec.colors.length !== segments * 3) {
        throw new Error(`addLines: colors needs 3 numbers per segment (${segments * 3}), got ${spec.colors.length}`);
    }
    const geom = new LineSegmentsGeometry();
    geom.setPositions(positions);
    if (spec.colors) {
        // Per segment in, per endpoint out.
        const src = Array.from(spec.colors);
        const out = new Float32Array(segments * 6);
        const c = new THREE.Color();
        for (let i = 0; i < segments; i++) {
            linear([src[i * 3], src[i * 3 + 1], src[i * 3 + 2]], c);
            out.set([c.r, c.g, c.b, c.r, c.g, c.b], i * 6);
        }
        geom.setColors(out);
    }
    const material = new LineMaterial({
        vertexColors: !!spec.colors,
        linewidth: spec.widthPx ?? 2,
        dashed: !!spec.dashed,
        dashSize: spec.dashed?.dash ?? 1,
        gapSize: spec.dashed?.gap ?? 1,
        depthWrite: false,
        // In front of the faces they run along (a stiffener on its plate), not hidden in them.
        polygonOffset: true,
        polygonOffsetFactor: -2,
        polygonOffsetUnits: -2,
    });
    if (!spec.colors) linear(spec.color ?? "#ffffff", material.color);
    const lines = new LineSegments2(geom, material);
    if (spec.dashed) lines.computeLineDistances();
    lines.renderOrder = 7;
    // LineMaterial turns linewidth into pixels from the drawing-buffer size.
    lines.onBeforeRender = (renderer) => renderer.getSize(material.resolution);
    return attach(owner, lines);
}

function labelTexture(label: SceneLabel): {texture: THREE.CanvasTexture; aspect: number} {
    const h = 64;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d")!;
    const font = `bold ${Math.round(h * 0.6)}px sans-serif`;
    ctx.font = font;
    const circle = label.shape === "circle";
    canvas.width = circle ? h : Math.max(h, Math.ceil(ctx.measureText(label.text).width + h * 0.5));
    canvas.height = h;
    if (label.background || label.border) {
        const inset = label.border ? 5 : 2;
        ctx.beginPath();
        if (circle) ctx.arc(h / 2, h / 2, h / 2 - inset, 0, Math.PI * 2);
        else ctx.rect(inset / 2, inset / 2, canvas.width - inset, h - inset);
        if (label.background) {
            ctx.fillStyle = label.background;
            ctx.fill();
        }
        if (label.border) {
            ctx.lineWidth = 5;
            ctx.strokeStyle = label.border;
            ctx.stroke();
        }
    }
    ctx.font = font; // resizing the canvas reset it
    ctx.fillStyle = label.color ?? "#0f172a";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label.text, canvas.width / 2, h / 2 + 2);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.minFilter = THREE.LinearFilter;
    return {texture, aspect: canvas.width / h};
}

export function addLabels(owner: string, labels: SceneLabel[], opts?: SceneLabelOptions): SceneOverlay {
    const group = new THREE.Group();
    group.name = `${owner}-labels`;
    const heightPx = opts?.heightPx ?? 18;
    for (const label of labels) {
        const {texture, aspect} = labelTexture(label);
        const sprite = new THREE.Sprite(
            new THREE.SpriteMaterial({map: texture, sizeAttenuation: false, depthTest: false, transparent: true}),
        );
        sprite.position.set(label.position[0], label.position[1], label.position[2]);
        sprite.renderOrder = 12;
        // A constant on-screen size: without size attenuation a sprite's scale is a fraction of the view
        // height (times the projection's focal term), so set it from the canvas' CSS height every frame.
        sprite.onBeforeRender = (renderer, _scene, camera) => {
            const cssHeight = Math.max(1, renderer.domElement.clientHeight);
            const s = (2 * heightPx) / (cssHeight * camera.projectionMatrix.elements[5]);
            sprite.scale.set(s * aspect, s, 1);
        };
        group.add(sprite);
    }
    return attach(owner, group);
}

export const hasFeaModel = (): boolean => feaMesh() !== null;

/** Colour elements of the active FEA mesh, over the model; null when no FEA model is loaded. */
export function paintFeaElements(owner: string, paint: SceneElementPaint): SceneOverlay | null {
    const mesh = feaMesh();
    const index = mesh?.geometry.getIndex();
    const pos = mesh?.geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!mesh || !index || !pos || !mesh.drawRanges) return null;

    // A non-indexed copy, so each element's triangles carry their own colour.
    const idx = index.array;
    const n = idx.length;
    const positions = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) positions.set([pos.getX(idx[i]), pos.getY(idx[i]), pos.getZ(idx[i])], i * 3);
    const colors = new Float32Array(n * 3);
    const keep = new Uint8Array(n);
    const c = new THREE.Color();
    const neutral = paint.neutral === undefined ? null : paint.neutral;
    const only = paint.only ? new Set(paint.only) : null;
    for (const [id, [start, count]] of mesh.drawRanges) {
        const m = /^E(\d+)$/.exec(id);
        if (!m) continue;
        const element = Number(m[1]);
        if (only && !only.has(element)) continue;
        const color = paint.colors.get(element) ?? neutral;
        if (color == null) continue;
        linear(color, c);
        for (let i = start; i < start + count && i < n; i++) {
            keep[i] = 1;
            colors.set([c.r, c.g, c.b], i * 3);
        }
    }
    // Triangles not painted collapse to a point (cheaper than a compacted copy, and the morph copy below
    // keeps one layout).
    const collapse = (a: Float32Array) => {
        for (let k = 0; k + 2 < n; k += 3) {
            if (keep[k]) continue;
            a.copyWithin((k + 1) * 3, k * 3, k * 3 + 3);
            a.copyWithin((k + 2) * 3, k * 3, k * 3 + 3);
        }
    };
    collapse(positions);
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    // The model's deformation, so the paint follows the warp.
    const morphs = mesh.geometry.morphAttributes.position as THREE.BufferAttribute[] | undefined;
    if (morphs?.length) {
        geom.morphAttributes.position = morphs.map((attr) => {
            const out = new Float32Array(n * 3);
            for (let i = 0; i < n; i++) out.set([attr.getX(idx[i]), attr.getY(idx[i]), attr.getZ(idx[i])], i * 3);
            collapse(out);
            return new THREE.BufferAttribute(out, 3);
        });
        geom.morphTargetsRelative = mesh.geometry.morphTargetsRelative;
    }
    const overlay = new THREE.Mesh(
        geom,
        new THREE.MeshBasicMaterial({
            vertexColors: true,
            side: THREE.DoubleSide,
            depthWrite: false,
            polygonOffset: true,
            polygonOffsetFactor: -4,
            polygonOffsetUnits: -16,
        }),
    );
    overlay.name = `${owner}-element-paint`;
    overlay.renderOrder = 2;
    // Shared, not copied: the warp slider writes the mesh's influences and the paint moves with them.
    overlay.morphTargetInfluences = mesh.morphTargetInfluences;
    overlay.morphTargetDictionary = mesh.morphTargetDictionary;
    return attach(owner, overlay);
}

/** Show only `elements` of the active FEA mesh (null: show all). */
export function isolateFeaElements(elements: Iterable<number> | null): void {
    const mesh = feaMesh();
    if (!mesh) return;
    if (elements == null) mesh.unhideAllDrawRanges?.();
    else mesh.isolateDrawRanges?.(new Set([...elements].map(rangeId)));
    requestRender();
}

/** Hide `elements` of the active FEA mesh, on top of what is hidden already. */
export function hideFeaElements(elements: Iterable<number>): void {
    feaMesh()?.hideBatchDrawRange?.([...elements].map(rangeId));
    requestRender();
}

/** The active FEA mesh's hidden elements. */
export function hiddenFeaElements(): number[] {
    const hidden = feaMesh()?.getHiddenRanges?.() ?? new Set<string>();
    const out: number[] = [];
    for (const id of hidden) {
        const m = /^E(\d+)$/.exec(id);
        if (m) out.push(Number(m[1]));
    }
    return out;
}

export function setFeaElementEdgesVisible(visible: boolean): void {
    useFeaAnimationStore.getState().setElementEdgesVisible(visible);
    requestRender();
}
