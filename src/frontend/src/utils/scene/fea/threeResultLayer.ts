// The result layer (viewport/api/resultLayer.ts) over a three.js mesh.
//
// The three.js viewport keeps drawing a result the way it always has: colours in the geometry's
// colour attribute, the displacement as morph target 0, the deformation scale as
// ``morphTargetInfluences[0]``. This module is those writes behind the interface the other viewport
// implements too, so the code that computes a step (nodalFieldPaint.ts) and the code that plays it
// (the animation driver, the transport) are the same for both.
//
// Two ways to a layer: `threeResultLayer` adopts a mesh that is already in the scene (the result
// mesh the streaming loader brought in as a GLB, with its selection and overlays), and
// `createThreeResultMesh` builds one from typed arrays.

import * as THREE from "three";

import type {ResultMeshData, ResultMeshLayer} from "@/viewport/api/resultLayer";
import {elementLocalMap} from "./elementLocalGeometry";
import {ensureVertexNormals, resetBasePositions, writeColorAttribute, writeMorphTarget} from "./gpuRefresh";
import {setSourceMorph} from "./sourceMorph";

type Drawn = THREE.Mesh | THREE.LineSegments | THREE.Points;

function eachMaterial(obj: Drawn, fn: (mat: THREE.Material) => void): void {
    const m = obj.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(m)) m.forEach(fn);
    else if (m) fn(m);
}

/** Switch a shader flag on every material slot that has it. CustomBatchedMesh holds an array of
 *  slots (original / selected overlay / invisible), and a flag set on the array itself is silently
 *  lost, so each slot is visited. */
function setFlag(obj: Drawn, flag: "vertexColors" | "morphTargets", on: boolean): void {
    eachMaterial(obj, (mat) => {
        const m = mat as unknown as Record<string, unknown>;
        if (flag in m && m[flag] !== on) {
            m[flag] = on;
            mat.needsUpdate = true;
        }
    });
}

class ThreeResultLayer implements ResultMeshLayer {
    /** Called by `remove`; an adopted mesh belongs to the loader that brought it in and has none. */
    onRemove: (() => void) | null = null;

    /** A plain colour given in place of the material's own (setColor), and the material's own
     *  while the given one is on it. */
    private plain: readonly [number, number, number] | null = null;
    private own: [number, number, number] | null = null;

    constructor(
        readonly object: Drawn,
        public basePositions: Float32Array,
    ) {}

    get vertexCount(): number {
        return this.basePositions.length / 3;
    }

    setColors(rgb: Float32Array): void {
        const geometry = this.object.geometry;
        // Through the element-local expansion when the geometry has one: its render vertices are
        // then not the source vertices `rgb` is numbered by.
        writeColorAttribute(geometry, rgb, elementLocalMap(geometry));
        setFlag(this.object, "vertexColors", true);
        if (this.plain) this.syncPlain();
    }

    setDisplacement(xyz: Float32Array): void {
        const object = this.object;
        const geometry = object.geometry;
        const renderToSource = elementLocalMap(geometry);
        // The position attribute holds the undeformed base and the morph carries the deformation;
        // a no-op (no copy, no upload) when it already holds this base.
        resetBasePositions(geometry, this.basePositions, renderToSource);
        // Additive morphing: position = base + influence * morph. Written in place, and the morph
        // texture refreshed without re-uploading the mesh (gpuRefresh.ts).
        writeMorphTarget(geometry, xyz, renderToSource);
        if (!object.morphTargetInfluences) object.morphTargetInfluences = [0];
        if (!object.morphTargetDictionary) object.morphTargetDictionary = {displacement: 0};
        setFlag(object, "morphTargets", true);
        // The element-edge wireframes index SOURCE vertices and share the position buffer this
        // geometry had before any element-local expansion, so they take the unexpanded deltas.
        if ((object as THREE.Mesh).isMesh) {
            setSourceMorph(object as THREE.Mesh, "fea-element-edges", xyz);
            setSourceMorph(object as THREE.Mesh, "fea-beam-element-edges", xyz);
        }
    }

    setInfluence(influence: number): void {
        const influences = this.object.morphTargetInfluences;
        if (influences) influences[0] = influence;
    }

    setColorsVisible(visible: boolean): void {
        if (this.object.geometry.getAttribute("color")) setFlag(this.object, "vertexColors", visible);
        if (this.plain) this.syncPlain();
    }

    setColor(rgb: readonly [number, number, number] | null): void {
        this.plain = rgb ? [rgb[0], rgb[1], rgb[2]] : null;
        this.syncPlain();
    }

    /** The given colour on the base material while no painted colours show (they are drawn times
     *  the material's colour), else the material's own colour back, exactly. */
    private syncPlain(): void {
        // Slot 0 on a CustomBatchedMesh: its other slots are the selection overlay and the
        // hidden-range material.
        const m = this.object.material as THREE.Material | THREE.Material[] | undefined;
        const mat = (Array.isArray(m) ? m[0] : m) as (THREE.Material & {color?: THREE.Color; vertexColors?: boolean}) | undefined;
        if (!mat?.color) return;
        if (this.plain && !mat.vertexColors) {
            this.own ??= [mat.color.r, mat.color.g, mat.color.b];
            mat.color.setRGB(this.plain[0], this.plain[1], this.plain[2]);
        } else if (this.own) {
            mat.color.setRGB(this.own[0], this.own[1], this.own[2]);
            this.own = null;
        }
    }

    setOpacity(opacity: number): void {
        const m = this.object.material as THREE.Material | THREE.Material[] | undefined;
        const mat = Array.isArray(m) ? m[0] : m;
        if (!mat || mat.opacity === opacity) return;
        mat.opacity = opacity;
        if (opacity < 1 && !mat.transparent) {
            mat.transparent = true;
            mat.needsUpdate = true;
        }
    }

    setVisible(visible: boolean): void {
        this.object.visible = visible;
    }

    remove(): void {
        this.onRemove?.();
        this.onRemove = null;
    }
}

const adopted = new WeakMap<Drawn, ThreeResultLayer>();

/** The layer of a mesh that is already in the scene; the same one for the same mesh.
 *  `basePositions` is the mesh's undeformed shape in source numbering. */
export function threeResultLayer(mesh: Drawn, basePositions: Float32Array): ResultMeshLayer {
    let layer = adopted.get(mesh);
    if (!layer) {
        layer = new ThreeResultLayer(mesh, basePositions);
        adopted.set(mesh, layer);
    } else {
        layer.basePositions = basePositions;
    }
    return layer;
}

/** A result mesh built from typed arrays, added to `parent`. `remove` takes it out and frees it. */
export function createThreeResultMesh(data: ResultMeshData, parent: THREE.Object3D): ResultMeshLayer & {object: THREE.Object3D} {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(data.positions), 3));
    if (data.indices) geometry.setIndex(new THREE.BufferAttribute(data.indices, 1));
    const color = new THREE.Color(...(data.color ?? (data.topology === "triangles" ? [1, 1, 1] : [0, 0, 0])));
    let object: Drawn;
    if (data.topology === "triangles" && data.overlay) {
        // Colours painted over elements: unlit, and in front of the faces they lie on.
        object = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
            color, side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -16,
        }));
        object.renderOrder = 2;
    } else if (data.topology === "triangles") {
        ensureVertexNormals(geometry);
        object = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({color, side: THREE.DoubleSide, metalness: 0, roughness: 1}));
    } else if (data.topology === "lines") {
        object = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({color}));
    } else {
        object = new THREE.Points(geometry, new THREE.PointsMaterial({color, size: data.sizePx ?? 4, sizeAttenuation: false}));
    }
    if (data.opacity !== undefined && data.opacity < 1) {
        eachMaterial(object, (mat) => {
            mat.transparent = true;
            mat.opacity = data.opacity!;
        });
    }
    if (data.overlay) {
        object.raycast = () => undefined;
        if (data.overlay.onTop) eachMaterial(object, (mat) => void (mat.depthTest = false));
    }
    if (data.matrix) {
        object.matrixAutoUpdate = false;
        object.matrix.fromArray(data.matrix);
    }
    parent.add(object);
    const layer = new ThreeResultLayer(object, new Float32Array(data.positions));
    layer.onRemove = () => {
        object.removeFromParent();
        geometry.dispose();
        eachMaterial(object, (mat) => mat.dispose());
    };
    return layer;
}
