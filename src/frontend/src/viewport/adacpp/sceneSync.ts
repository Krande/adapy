// Mirrors the three.js scene into the adacpp renderer: geometry is uploaded once per buffer version,
// instance state (transform, colour, flags, morph influence) and per-range state (hidden, selected)
// are pushed as they change. Everything three.js would draw on the camera's layers is mirrored,
// except what the adacpp renderer cannot draw yet (see adacppViewport.ts).
import * as THREE from "three";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {selectedMaterial} from "@/utils/default_materials";
import {InstanceFlag, SegmentFlag, Topology, type AdacppModule, type WebRendererApi} from "./types";

type SegmentKind = "batched" | "groups" | "edges" | "none";

interface Entry {
    obj: THREE.Object3D;
    signature: string;
    geometry: number;
    instances: number[];
    topology: number;
    segmentKind: SegmentKind;
    segmentIds: ReadonlyArray<string> | null; // batched: range id per segment
    segmentRangeIdx: Int32Array | null; // edges: rangeId per segment
    stateVersion: number;
    // Per-vertex data that changes in place, tracked by identity AND version: applyField installs a
    // fresh morph BufferAttribute on every FEA step, which starts at the version of the one it
    // replaces, so the version alone cannot tell the swap apart.
    colorAttr: object | undefined;
    colorVersion: number;
    morphAttr: object | undefined;
    morphVersion: number;
    seen: number;
}

type AnyMaterial = THREE.Material & {
    color?: THREE.Color;
    vertexColors?: boolean;
    flatShading?: boolean;
    uniforms?: Record<string, {value: unknown}>;
};

function attrVersion(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute | undefined): number {
    if (!a) return -1;
    return (a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
        ? (a as THREE.InterleavedBufferAttribute).data.version
        : (a as THREE.BufferAttribute).version;
}

/** Any position-like attribute as a tightly packed Float32Array of `size` components. */
function floats(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, size = 3): Float32Array {
    const plain = a as THREE.BufferAttribute;
    if (!(a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute && plain.array instanceof Float32Array &&
        a.itemSize === size && !a.normalized && plain.array.length === a.count * size) {
        return plain.array;
    }
    const out = new Float32Array(a.count * size);
    for (let i = 0; i < a.count; i++) {
        out[i * size] = a.getX(i);
        if (size > 1) out[i * size + 1] = a.getY(i);
        if (size > 2) out[i * size + 2] = a.getZ(i);
    }
    return out;
}

/** Vertex colours as packed linear RGBA8 (three.js treats vertex colours as linear). */
function packedColors(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute): Uint32Array {
    const out = new Uint32Array(a.count);
    const q = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)));
    const hasAlpha = a.itemSize >= 4;
    for (let i = 0; i < a.count; i++) {
        out[i] = (q(a.getX(i)) | (q(a.getY(i)) << 8) | (q(a.getZ(i)) << 16) | (q(hasAlpha ? a.getW(i) : 1) << 24)) >>> 0;
    }
    return out;
}

function packColor(c: THREE.Color, alpha = 1): number {
    const q = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)));
    return (q(c.r) | (q(c.g) << 8) | (q(c.b) << 16) | (q(alpha) << 24)) >>> 0;
}

function baseMaterial(obj: THREE.Object3D): AnyMaterial | undefined {
    if (obj instanceof CustomBatchedMesh) return obj.originalMaterial as AnyMaterial;
    const m = (obj as THREE.Mesh).material as AnyMaterial | AnyMaterial[] | undefined;
    return Array.isArray(m) ? m[0] : m;
}

function materialColor(m: AnyMaterial | undefined, fallback: number): THREE.Color {
    if (m?.color instanceof THREE.Color) return m.color;
    const u = m?.uniforms;
    for (const key of ["color", "diffuse", "uColor"]) {
        const v = u?.[key]?.value;
        if (v instanceof THREE.Color) return v;
    }
    return new THREE.Color(fallback);
}

function isUnlit(m: AnyMaterial | undefined): boolean {
    if (!m) return false;
    const t = m as unknown as Record<string, boolean>;
    return !!(t.isMeshBasicMaterial || t.isLineBasicMaterial || t.isLineDashedMaterial || t.isPointsMaterial ||
        t.isShaderMaterial || t.isLineMaterial);
}

export class SceneSync {
    private readonly entries = new Map<string, Entry>();
    private readonly byInstance = new Map<number, Entry>();
    private frame = 0;
    private readonly matrixPtr: number;
    private readonly viewPtr: number;
    private readonly projPtr: number;
    private readonly bg = new THREE.Color();
    lastSyncMs = 0;

    constructor(private readonly module: AdacppModule, private readonly r: WebRendererApi) {
        this.matrixPtr = module._malloc(64);
        this.viewPtr = module._malloc(64);
        this.projPtr = module._malloc(64);
    }

    dispose(): void {
        this.entries.clear();
        this.byInstance.clear();
        this.module._free(this.matrixPtr);
        this.module._free(this.viewPtr);
        this.module._free(this.projPtr);
    }

    sync(scene: THREE.Scene, camera: THREE.Camera): void {
        const t0 = performance.now();
        this.frame++;
        scene.updateMatrixWorld();
        if (camera.parent === null) camera.updateMatrixWorld();

        if (scene.background instanceof THREE.Color) {
            this.bg.copy(scene.background).convertLinearToSRGB();
            this.r.setBackground(this.bg.r, this.bg.g, this.bg.b);
        }

        scene.traverseVisible((obj) => {
            if (!camera.layers.test(obj.layers)) return;
            if (obj.parent instanceof CustomBatchedMesh && obj.parent.isHighlightOverlay(obj)) return;
            const o = obj as unknown as Record<string, boolean>;
            if (o.isSprite) return;
            if (o.isMesh || o.isLineSegments || o.isLine || o.isPoints) this.syncObject(obj);
        });

        for (const [key, e] of this.entries) {
            if (e.seen !== this.frame) {
                this.drop(e);
                this.entries.delete(key);
            }
        }

        const heap = this.module.HEAPF32;
        heap.set(camera.matrixWorldInverse.elements, this.viewPtr >> 2);
        heap.set(camera.projectionMatrix.elements, this.projPtr >> 2);
        this.r.setCamera(this.viewPtr, this.projPtr, !!(camera as THREE.OrthographicCamera).isOrthographicCamera);
        this.lastSyncMs = performance.now() - t0;
    }

    /** The scene object an instance draws, and which of its segments' range ids is which. */
    entryForInstance(instance: number): {obj: THREE.Object3D; segmentIds: ReadonlyArray<string> | null} | null {
        const e = this.byInstance.get(instance);
        return e ? {obj: e.obj, segmentIds: e.segmentIds} : null;
    }

    private drop(e: Entry): void {
        for (const id of e.instances) {
            this.r.removeInstance(id);
            this.byInstance.delete(id);
        }
        this.r.removeGeometry(e.geometry);
    }

    private syncObject(obj: THREE.Object3D): void {
        const o = obj as unknown as Record<string, boolean>;
        const geometry = (obj as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
        if (!geometry?.attributes) return;
        const mat = baseMaterial(obj);
        const fatLines = !!(o.isLineSegments2 || o.isLine2);
        const position = fatLines ? geometry.attributes.instanceStart : geometry.attributes.position;
        if (!position || position.count === 0) return;

        const topology = o.isPoints ? Topology.Points : (o.isLine || o.isLineSegments || fatLines) ? Topology.Lines : Topology.Triangles;
        const flat = topology === Topology.Triangles && !!mat?.flatShading;
        const groupsSig = obj instanceof CustomBatchedMesh ? "b" : `${geometry.groups.length}`;
        const signature = `${geometry.uuid}:${attrVersion(position)}:${geometry.index?.version ?? -1}:${topology}:${flat}:${groupsSig}`;

        let e = this.entries.get(obj.uuid);
        if (e && e.signature !== signature) {
            this.drop(e);
            this.entries.delete(obj.uuid);
            e = undefined;
        }
        if (!e) {
            const created = this.upload(obj, geometry, topology, flat, fatLines);
            if (!created) return;
            const morph0 = fatLines ? undefined : geometry.morphAttributes.position?.[0];
            e = {
                ...created, obj, signature, stateVersion: -1, seen: 0,
                colorAttr: geometry.attributes.color, colorVersion: attrVersion(geometry.attributes.color),
                morphAttr: morph0, morphVersion: attrVersion(morph0),
            };
            this.entries.set(obj.uuid, e);
            for (const id of e.instances) this.byInstance.set(id, e);
        }
        e.seen = this.frame;

        // Per-vertex data that changes in place (FEA field colours, warp).
        const colorAttr = geometry.attributes.color;
        if (colorAttr && !fatLines && (colorAttr !== e.colorAttr || attrVersion(colorAttr) !== e.colorVersion)) {
            e.colorAttr = colorAttr;
            e.colorVersion = attrVersion(colorAttr);
            this.withU32(packedColors(colorAttr), (p, n) => this.r.updateColors(e!.geometry, p, n));
        }
        const morphAttr = fatLines ? undefined : geometry.morphAttributes.position?.[0];
        if (morphAttr && (morphAttr !== e.morphAttr || attrVersion(morphAttr) !== e.morphVersion)) {
            e.morphAttr = morphAttr;
            e.morphVersion = attrVersion(morphAttr);
            this.withF32(floats(morphAttr), (p) => this.r.updateMorph(e!.geometry, p, morphAttr.count));
        }

        this.pushInstances(obj, e, mat, geometry);
        this.pushSegmentStates(obj, e);
    }

    private upload(
        obj: THREE.Object3D,
        geometry: THREE.BufferGeometry,
        topology: number,
        flat: boolean,
        fatLines: boolean,
    ): Pick<Entry, "geometry" | "instances" | "topology" | "segmentKind" | "segmentIds" | "segmentRangeIdx"> | null {
        let positions: Float32Array;
        let colors: Uint32Array | null = null;
        let indices: Uint32Array | null = null;
        if (fatLines) {
            // LineSegments2: one instance per segment, endpoints in instanceStart / instanceEnd.
            const s = geometry.attributes.instanceStart, en = geometry.attributes.instanceEnd;
            positions = new Float32Array(s.count * 6);
            for (let i = 0; i < s.count; i++) {
                positions.set([s.getX(i), s.getY(i), s.getZ(i), en.getX(i), en.getY(i), en.getZ(i)], i * 6);
            }
            const cs = geometry.attributes.instanceColorStart, ce = geometry.attributes.instanceColorEnd;
            if (cs && ce) {
                const a = packedColors(cs), b = packedColors(ce);
                colors = new Uint32Array(s.count * 2);
                for (let i = 0; i < s.count; i++) {
                    colors[i * 2] = a[i];
                    colors[i * 2 + 1] = b[i];
                }
            }
        } else {
            positions = floats(geometry.attributes.position);
            if (geometry.attributes.color) colors = packedColors(geometry.attributes.color);
            if (geometry.index) {
                const ia = geometry.index.array;
                indices = ia instanceof Uint32Array ? ia : Uint32Array.from(ia as ArrayLike<number>);
            }
            if ((obj as THREE.Line).isLine && !(obj as THREE.LineSegments).isLineSegments) {
                // Line strip -> segment list.
                const n = indices ? indices.length : positions.length / 3;
                const strip = indices ?? Uint32Array.from({length: n}, (_, i) => i);
                const seg = new Uint32Array(Math.max(0, n - 1) * 2);
                for (let i = 0; i + 1 < n; i++) {
                    seg[i * 2] = strip[i];
                    seg[i * 2 + 1] = strip[i + 1];
                }
                indices = seg;
            }
        }
        const vertexCount = positions.length / 3;
        const normals = topology === Topology.Triangles && !flat && geometry.attributes.normal && !fatLines
            ? floats(geometry.attributes.normal) : null;
        const morphAttr = fatLines ? undefined : geometry.morphAttributes.position?.[0];
        const morph = morphAttr ? floats(morphAttr) : null;

        // Segments: the ranges per-object state applies to.
        let segmentKind: SegmentKind = "none";
        let segments: Uint32Array | null = null;
        let segmentIds: ReadonlyArray<string> | null = null;
        let segmentRangeIdx: Int32Array | null = null;
        const indexCount = indices ? indices.length : vertexCount;
        if (obj instanceof CustomBatchedMesh) {
            const segs = obj.getSortedSegments();
            const list: number[] = [];
            const ids: string[] = [];
            let cursor = 0;
            for (let i = 0; i < segs.ids.length; i++) {
                const start = segs.starts[i], count = segs.counts[i];
                if (start > cursor) { // gaps draw with the default material, like updateGroups does
                    list.push(cursor, start - cursor);
                    ids.push("");
                }
                list.push(start, count);
                ids.push(segs.ids[i]);
                cursor = Math.max(cursor, start + count);
            }
            if (cursor < indexCount) {
                list.push(cursor, indexCount - cursor);
                ids.push("");
            }
            segments = Uint32Array.from(list);
            segmentIds = ids;
            segmentKind = "batched";
        } else if (topology === Topology.Lines && geometry.attributes.rangeId && !indices &&
            (baseMaterial(obj)?.uniforms?.uVisibleTex)) {
            // CAD edge overlay: contiguous runs of the per-vertex rangeId.
            const rid = geometry.attributes.rangeId;
            const list: number[] = [];
            const idx: number[] = [];
            let start = 0;
            for (let i = 1; i <= rid.count; i++) {
                if (i === rid.count || rid.getX(i) !== rid.getX(start)) {
                    list.push(start, i - start);
                    idx.push(rid.getX(start));
                    start = i;
                }
            }
            segments = Uint32Array.from(list);
            segmentRangeIdx = Int32Array.from(idx);
            segmentKind = "edges";
        } else if (geometry.groups.length > 0 && Array.isArray((obj as THREE.Mesh).material)) {
            segments = Uint32Array.from(geometry.groups.flatMap((g) => [g.start, Math.min(g.count, indexCount - g.start)]));
            segmentKind = "groups";
        }

        const M = this.module;
        const alloc = (a: Float32Array | Uint32Array | null): number => {
            if (!a || a.length === 0) return 0;
            const p = M._malloc(a.byteLength);
            (a instanceof Float32Array ? M.HEAPF32 : M.HEAPU32).set(a, p >> 2);
            return p;
        };
        const ptrs = [positions, normals, colors, morph, indices, segments].map(alloc);
        let geom: number;
        try {
            geom = this.r.addGeometry(
                topology, ptrs[0], vertexCount, ptrs[1], ptrs[2], ptrs[3], ptrs[4], indices ? indices.length : 0,
                ptrs[5], segments ? segments.length / 2 : 0,
            );
        } finally {
            for (const p of ptrs) if (p) M._free(p);
        }

        const count = (obj as THREE.InstancedMesh).isInstancedMesh ? (obj as THREE.InstancedMesh).count : 1;
        const instances: number[] = [];
        for (let i = 0; i < count; i++) instances.push(this.r.addInstance(geom));
        return {geometry: geom, instances, topology, segmentKind, segmentIds, segmentRangeIdx};
    }

    private readonly tmpMatrix = new THREE.Matrix4();

    private pushInstances(obj: THREE.Object3D, e: Entry, mat: AnyMaterial | undefined, geometry: THREE.BufferGeometry) {
        const color = materialColor(mat, e.topology === Topology.Triangles ? 0xffffff : 0x000000);
        const opacity = mat?.opacity ?? 1;
        let flags = 0;
        const hasColors = !!geometry.attributes.color || !!geometry.attributes.instanceColorStart;
        if (hasColors && (mat?.vertexColors || (mat as unknown as {isShaderMaterial?: boolean})?.isShaderMaterial)) {
            flags |= InstanceFlag.VertexColors;
        }
        if (isUnlit(mat) || e.topology !== Topology.Triangles) flags |= InstanceFlag.Unlit;
        if (mat?.side === THREE.DoubleSide || mat?.side === THREE.BackSide) flags |= InstanceFlag.DoubleSided;
        if (mat?.transparent && opacity < 1) flags |= InstanceFlag.Transparent;
        if (mat && !mat.visible) flags |= InstanceFlag.Hidden;
        if (geometry.morphTargetsRelative) flags |= InstanceFlag.MorphRelative;
        // Like three.js' GpuMeshPicker, only model meshes are pickable: edges, grid, overlays and
        // helpers must never stand in front of the object a click is meant for.
        if (!(obj instanceof CustomBatchedMesh)) flags |= InstanceFlag.NoPick;
        const influence = (obj as THREE.Mesh).morphTargetInfluences?.[0] ?? 0;
        // MeshStandardMaterial's metalness/roughness; Lambert/Phong light as diffuse-only.
        const pbr = mat as unknown as {isMeshStandardMaterial?: boolean; metalness?: number; roughness?: number};
        const specular = !!pbr?.isMeshStandardMaterial;
        const metalness = specular ? (pbr.metalness ?? 0) : 0;
        const roughness = specular ? (pbr.roughness ?? 1) : 1;

        const heap = this.module.HEAPF32;
        const inst = obj as THREE.InstancedMesh;
        for (let i = 0; i < e.instances.length; i++) {
            if (inst.isInstancedMesh) {
                inst.getMatrixAt(i, this.tmpMatrix);
                this.tmpMatrix.premultiply(obj.matrixWorld);
                heap.set(this.tmpMatrix.elements, this.matrixPtr >> 2);
            } else {
                heap.set(obj.matrixWorld.elements, this.matrixPtr >> 2);
            }
            this.r.setInstance(
                e.instances[i], this.matrixPtr, color.r, color.g, color.b, opacity, flags, influence,
                metalness, roughness, specular,
            );
        }
    }

    private pushSegmentStates(obj: THREE.Object3D, e: Entry): void {
        let packed: Uint32Array | null = null;
        if (e.segmentKind === "batched" && obj instanceof CustomBatchedMesh) {
            if (obj.renderStateVersion === e.stateVersion) return;
            e.stateVersion = obj.renderStateVersion;
            const ids = e.segmentIds!;
            const highlight = packColor(selectedMaterial.color);
            packed = new Uint32Array(ids.length * 2);
            for (let s = 0; s < ids.length; s++) {
                const id = ids[s];
                if (!id) continue;
                if (obj.isRangeHidden(id)) packed[s * 2 + 1] = SegmentFlag.Hidden;
                else if (obj.isRangeSelected(id)) packed[s * 2] = highlight;
            }
        } else if (e.segmentKind === "edges") {
            const tex = baseMaterial(obj)?.uniforms?.uVisibleTex?.value as THREE.DataTexture | undefined;
            if (!tex || tex.version === e.stateVersion) return;
            e.stateVersion = tex.version;
            const vis = tex.image.data as Uint8Array;
            const idx = e.segmentRangeIdx!;
            packed = new Uint32Array(idx.length * 2);
            for (let s = 0; s < idx.length; s++) {
                if (vis[idx[s]] === 0) packed[s * 2 + 1] = SegmentFlag.Hidden;
            }
        } else if (e.segmentKind === "groups") {
            const mats = (obj as THREE.Mesh).material as AnyMaterial[];
            const groups = ((obj as THREE.Mesh).geometry as THREE.BufferGeometry).groups;
            packed = new Uint32Array(groups.length * 2);
            for (let s = 0; s < groups.length; s++) {
                const m = mats[groups[s].materialIndex ?? 0];
                if (!m || !m.visible) packed[s * 2 + 1] = SegmentFlag.Hidden;
                else if (m.color instanceof THREE.Color) packed[s * 2] = packColor(m.color);
            }
        }
        if (!packed) return;
        const p = this.module._malloc(packed.byteLength);
        this.module.HEAPU32.set(packed, p >> 2);
        try {
            for (const id of e.instances) this.r.setSegmentStates(id, p, packed.length / 2);
        } finally {
            this.module._free(p);
        }
    }

    private withU32(a: Uint32Array, fn: (ptr: number, count: number) => void): void {
        const p = this.module._malloc(a.byteLength);
        this.module.HEAPU32.set(a, p >> 2);
        try {
            fn(p, a.length);
        } finally {
            this.module._free(p);
        }
    }

    private withF32(a: Float32Array, fn: (ptr: number, count: number) => void): void {
        const p = this.module._malloc(a.byteLength);
        this.module.HEAPF32.set(a, p >> 2);
        try {
            fn(p, a.length);
        } finally {
            this.module._free(p);
        }
    }
}
