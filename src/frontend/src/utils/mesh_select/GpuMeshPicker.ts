import * as THREE from "three";
import {cameraRef, rendererRef, sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "./CustomBatchedMesh";

// GPU face-picker for CustomBatchedMesh.
//
// First attempt at this used a per-vertex ``pickColor`` attribute on
// the original (indexed) geometry. That broke on FEA meshes because
// FEA elements share vertices — each drawRange overwrote the shared
// vertex's colour, so a triangle's three corners ended up coloured
// for three different elements and the GPU interpolated across them.
// The pixel decoded to a blend that didn't match any registered id
// (or matched the wrong one consistently). CAD models hide the bug
// because each component is its own primitive with non-shared
// vertex ranges.
//
// Current design: each registered CustomBatchedMesh gets a separate
// **non-indexed** child mesh on layer 31. The child has one set of
// 3 unique vertices per triangle, all coloured with the rangeId's
// encoded RGB. Interpolation across the triangle is now a constant,
// readback is exact. We render only layer 31 to a small offscreen
// FBO, read one pixel, decode.
//
// Memory cost: ~3× the visible vertex count. For a 3M-tri mesh that's
// ~135MB extra GPU memory. Tolerable for current sizes; switch to a
// flat-varying GLSL3 shader + provoking-vertex duplication if memory
// becomes the constraint.
//
// Morph support: morph delta attrs are duplicated into the picker
// geometry the same way positions are, then driven by the source
// mesh's ``morphTargetInfluences`` (shared by reference). The picker
// renders the deformed shape, so clicks on a scrubbed FEA mesh land
// on the visible element — without this the picker would silently
// fall back to the raycast path for every FEA click, since FEA
// streaming sets ``morphTargetInfluences[0]`` to the displacement
// scale (default 1) at load time and never zeroes it.

export type GpuMeshPickResult = {
    mesh: CustomBatchedMesh;
    rangeId: string;
    worldPosition: THREE.Vector3;
} | null;

interface RegisteredMesh {
    /** Layer-31 child mesh holding the non-indexed picker geometry. */
    pickerMesh: THREE.Mesh;
    /** Number of morph targets duplicated into the picker geometry,
     *  if any. Used to decide whether to refresh ``morphTargetInfluences``
     *  on the picker each pick (cheap, but skip when no morph). */
    morphTargetCount: number;
}

interface IdEntry {
    mesh: CustomBatchedMesh;
    rangeId: string;
    /** First original-geometry vertex referenced by this range —
     *  used as a stable reference for the click's world position. */
    firstVertexIndex: number;
}

// Layer 31: picker-only. Camera enables only this layer during the
// pick render so visible meshes are skipped and only picker meshes
// rasterize. Any number > 0 that no other code uses works; 31 sits
// at the top of the 32-bit mask to stay out of the way.
const PICK_LAYER = 31;

class GpuMeshPicker {
    private rt: THREE.WebGLRenderTarget | null = null;
    private size = new THREE.Vector2(1, 1);
    private idCounter = 1; // 0 reserved for background

    private idToEntry = new Map<number, IdEntry>();
    private registered = new WeakMap<CustomBatchedMesh, RegisteredMesh>();

    private ensureRT(renderer: THREE.WebGLRenderer): void {
        renderer.getSize(this.size);
        const w = Math.max(1, Math.floor(this.size.x));
        const h = Math.max(1, Math.floor(this.size.y));
        if (!this.rt || this.rt.width !== w || this.rt.height !== h) {
            if (this.rt) this.rt.dispose();
            this.rt = new THREE.WebGLRenderTarget(w, h, {
                depthBuffer: true,
                stencilBuffer: false,
                type: THREE.UnsignedByteType,
                format: THREE.RGBAFormat,
            });
        }
    }

    private makePickingMaterial(morphTargetCount: number): THREE.ShaderMaterial {
        // pickColor → fragment color. Morph chunks pulled in from
        // three's library so we get the same morph kernel the visible
        // material uses, without having to reimplement the influence
        // blend / morph-texture-vs-attribute decision.
        const vertex = `
            precision mediump float;
            #include <common>
            #include <morphtarget_pars_vertex>
            attribute vec3 pickColor;
            varying vec3 vPick;
            void main() {
                vPick = pickColor;
                vec3 transformed = position;
                #include <morphtarget_vertex>
                gl_Position = projectionMatrix * modelViewMatrix * vec4(transformed, 1.0);
            }
        `;
        const fragment = `
            precision mediump float;
            varying vec3 vPick;
            void main() {
                gl_FragColor = vec4(vPick, 1.0);
            }
        `;
        const sm = new THREE.ShaderMaterial({
            vertexShader: vertex,
            fragmentShader: fragment,
            side: THREE.DoubleSide,
            depthTest: true,
            depthWrite: true,
        });
        // Three.js gates the morph code path on this flag. Without it
        // the morphtarget_vertex chunk compiles to a no-op even when
        // the geometry has morphAttributes.
        if (morphTargetCount > 0) {
            (sm as any).morphTargets = true;
        }
        return sm;
    }

    /** Build the non-indexed picker child mesh for this
     *  CustomBatchedMesh. Idempotent — re-registration short-circuits. */
    registerMesh(mesh: CustomBatchedMesh): void {
        if (this.registered.has(mesh)) return;
        const geom = mesh.geometry as THREE.BufferGeometry;
        if (!geom.index || !geom.attributes.position) return;
        if (mesh.drawRanges.size === 0) return;

        const t0 = performance.now();
        const indices = geom.index.array as Uint16Array | Uint32Array;
        const posAttr = geom.attributes.position as THREE.BufferAttribute;
        const posArr = posAttr.array as Float32Array;
        const itemSize = posAttr.itemSize; // typically 3
        const nTris = (geom.index.count / 3) | 0;

        // Per-triangle colour map: walk each drawRange, allocate one
        // global id per range, fan its colour out to every triangle
        // the range owns. Triangles outside all ranges stay at colour
        // (0, 0, 0) = background and decode to null on hit.
        const triColor = new Uint8Array(nTris * 3);
        for (const [rangeId, [start, count]] of mesh.drawRanges) {
            if (count <= 0) continue;
            const id = this.idCounter++;
            const r = id & 0xff;
            const g = (id >> 8) & 0xff;
            const b = (id >> 16) & 0xff;
            const startTri = (start / 3) | 0;
            const triCount = (count / 3) | 0;
            this.idToEntry.set(id, {
                mesh,
                rangeId,
                firstVertexIndex: indices[start],
            });
            for (let t = 0; t < triCount; t++) {
                const ti = (startTri + t) * 3;
                triColor[ti] = r;
                triColor[ti + 1] = g;
                triColor[ti + 2] = b;
            }
        }

        // Build non-indexed picker geometry: 3 unique vertices per
        // triangle, all carrying the same pickColor. Direct typed-
        // array reads beat ``posAttr.getX/Y/Z`` on the hot loop —
        // matters for million-tri models.
        const pickerPositions = new Float32Array(nTris * 9);
        const pickerColors = new Uint8Array(nTris * 9);
        for (let ti = 0; ti < nTris; ti++) {
            const i0 = indices[ti * 3] * itemSize;
            const i1 = indices[ti * 3 + 1] * itemSize;
            const i2 = indices[ti * 3 + 2] * itemSize;
            const offP = ti * 9;
            pickerPositions[offP + 0] = posArr[i0];
            pickerPositions[offP + 1] = posArr[i0 + 1];
            pickerPositions[offP + 2] = posArr[i0 + 2];
            pickerPositions[offP + 3] = posArr[i1];
            pickerPositions[offP + 4] = posArr[i1 + 1];
            pickerPositions[offP + 5] = posArr[i1 + 2];
            pickerPositions[offP + 6] = posArr[i2];
            pickerPositions[offP + 7] = posArr[i2 + 1];
            pickerPositions[offP + 8] = posArr[i2 + 2];

            const r = triColor[ti * 3];
            const g = triColor[ti * 3 + 1];
            const b = triColor[ti * 3 + 2];
            // Same RGB on all three picker vertices of this triangle.
            // Interpolation across the triangle is now constant.
            pickerColors[offP + 0] = r;
            pickerColors[offP + 1] = g;
            pickerColors[offP + 2] = b;
            pickerColors[offP + 3] = r;
            pickerColors[offP + 4] = g;
            pickerColors[offP + 5] = b;
            pickerColors[offP + 6] = r;
            pickerColors[offP + 7] = g;
            pickerColors[offP + 8] = b;
        }

        const pickerGeom = new THREE.BufferGeometry();
        pickerGeom.setAttribute(
            "position",
            new THREE.BufferAttribute(pickerPositions, 3),
        );
        // ``normalized: true`` so the shader sees 0..1 floats from
        // the Uint8 storage automatically.
        pickerGeom.setAttribute(
            "pickColor",
            new THREE.BufferAttribute(pickerColors, 3, true),
        );

        // Duplicate the source morph delta attributes into the
        // non-indexed picker layout so the picker rasterises the
        // **deformed** geometry — without this, FEA models (which
        // ship with displacementScale=1 active from the moment
        // applyField runs) would render the un-deformed shape and
        // every click would land in the wrong screen pixel.
        const sourceMorphs = geom.morphAttributes?.position as
            | THREE.BufferAttribute[]
            | undefined;
        const morphTargetCount = sourceMorphs?.length ?? 0;
        const morphMemBytes = (() => {
            if (!sourceMorphs || morphTargetCount === 0) return 0;
            pickerGeom.morphAttributes.position = [];
            let total = 0;
            for (let m = 0; m < morphTargetCount; m++) {
                const src = sourceMorphs[m];
                const srcArr = src.array as Float32Array;
                const srcItem = src.itemSize; // typically 3
                const dup = new Float32Array(nTris * 9);
                for (let ti = 0; ti < nTris; ti++) {
                    const i0 = indices[ti * 3] * srcItem;
                    const i1 = indices[ti * 3 + 1] * srcItem;
                    const i2 = indices[ti * 3 + 2] * srcItem;
                    const offM = ti * 9;
                    dup[offM + 0] = srcArr[i0];
                    dup[offM + 1] = srcArr[i0 + 1];
                    dup[offM + 2] = srcArr[i0 + 2];
                    dup[offM + 3] = srcArr[i1];
                    dup[offM + 4] = srcArr[i1 + 1];
                    dup[offM + 5] = srcArr[i1 + 2];
                    dup[offM + 6] = srcArr[i2];
                    dup[offM + 7] = srcArr[i2 + 1];
                    dup[offM + 8] = srcArr[i2 + 2];
                }
                pickerGeom.morphAttributes.position.push(
                    new THREE.BufferAttribute(dup, 3),
                );
                total += dup.byteLength;
            }
            pickerGeom.morphTargetsRelative = geom.morphTargetsRelative === true;
            return total;
        })();

        const pickerMesh = new THREE.Mesh(
            pickerGeom,
            this.makePickingMaterial(morphTargetCount),
        );
        pickerMesh.name = `__pick__${mesh.name}`;
        pickerMesh.frustumCulled = mesh.frustumCulled;
        pickerMesh.layers.set(PICK_LAYER);
        // Child of the original mesh so it inherits matrixWorld
        // automatically — pickerMesh moves with its parent on any
        // scene-graph transform update.
        mesh.add(pickerMesh);

        this.registered.set(mesh, {pickerMesh, morphTargetCount});

        const memMB =
            (pickerPositions.byteLength + pickerColors.byteLength + morphMemBytes)
            / 1024 / 1024;
        const dt = performance.now() - t0;
        console.info(
            `[GpuMeshPicker] built picker for "${mesh.name}" in ${dt.toFixed(1)}ms ` +
            `(${nTris} tris, ${morphTargetCount} morph(s), ~${memMB.toFixed(1)}MB)`,
        );
    }

    /** Compute the world-space position of the picked range's first
     *  vertex, applying CPU morph blending if morph attrs exist on
     *  the source geometry. Single vertex — microseconds. */
    private computeWorldPosition(
        mesh: CustomBatchedMesh,
        index: number,
    ): THREE.Vector3 {
        const geom = mesh.geometry as THREE.BufferGeometry;
        const pos = geom.attributes.position as THREE.BufferAttribute;
        const v = new THREE.Vector3(pos.getX(index), pos.getY(index), pos.getZ(index));
        const morphs = geom.morphAttributes?.position as THREE.BufferAttribute[] | undefined;
        const rel = geom.morphTargetsRelative === true;
        const infl = (mesh as any).morphTargetInfluences as number[] | undefined;
        if (morphs && infl && morphs.length === infl.length) {
            let sum = 0;
            for (let i = 0; i < morphs.length; i++) {
                const inf = infl[i] || 0;
                if (inf === 0) continue;
                sum += inf;
                const mp = morphs[i];
                const mx = mp.getX(index);
                const my = mp.getY(index);
                const mz = mp.getZ(index);
                if (rel) {
                    v.x += mx * inf;
                    v.y += my * inf;
                    v.z += mz * inf;
                } else {
                    v.x = v.x * (1 - sum) + mx * inf;
                    v.y = v.y * (1 - sum) + my * inf;
                    v.z = v.z * (1 - sum) + mz * inf;
                }
            }
        }
        return v.applyMatrix4(mesh.matrixWorld);
    }

    /** Run a GPU pick at the given client coordinates. Returns the
     *  picked mesh + rangeId, or null on miss / morph-active. */
    pickAt(clientX: number, clientY: number): GpuMeshPickResult {
        const renderer = rendererRef.current;
        const scene = sceneRef.current;
        const camera = cameraRef.current as THREE.PerspectiveCamera | null;
        if (!renderer || !scene || !camera) return null;

        // Lazy registration: any CustomBatchedMesh in the scene that
        // isn't yet built gets its picker mesh now. First click pays
        // the cost; later clicks are free. While walking, also
        // re-link each picker mesh's morphTargetInfluences to the
        // source mesh's array (by reference, so subsequent influence
        // mutations are reflected without a relink). Cheap — one
        // assignment per registered mesh per pick.
        scene.traverse((o) => {
            if (!(o instanceof CustomBatchedMesh)) return;
            this.registerMesh(o);
            const reg = this.registered.get(o);
            if (!reg || reg.morphTargetCount === 0) return;
            const srcInfl = (o as any).morphTargetInfluences as number[] | undefined;
            if (srcInfl) {
                (reg.pickerMesh as any).morphTargetInfluences = srcInfl;
            }
            const srcDict = (o as any).morphTargetDictionary;
            if (srcDict) {
                (reg.pickerMesh as any).morphTargetDictionary = srcDict;
            }
        });

        this.ensureRT(renderer);

        const canvas = renderer.domElement;
        const rect = canvas.getBoundingClientRect();
        if (
            clientX < rect.left || clientX > rect.right
            || clientY < rect.top || clientY > rect.bottom
        ) {
            return null;
        }

        // Render only layer 31 — the visible meshes are on layer 0,
        // edges on layer 1, picker meshes on layer 31. Camera mask
        // restored in the finally so subsequent main renders aren't
        // affected.
        const prevLayers = camera.layers.mask;
        camera.layers.disableAll();
        camera.layers.enable(PICK_LAYER);

        const prevTarget = renderer.getRenderTarget();
        const prevClear = renderer.getClearColor(new THREE.Color());
        const prevAlpha = renderer.getClearAlpha();

        let pixel: Uint8Array | null = null;
        try {
            renderer.setRenderTarget(this.rt);
            renderer.setClearColor(0x000000, 0);
            renderer.clear();
            renderer.render(scene, camera);

            const rx = this.rt!.width / Math.max(1, rect.width);
            const ry = this.rt!.height / Math.max(1, rect.height);
            let x = Math.floor((clientX - rect.left) * rx);
            let y = Math.floor((rect.bottom - clientY) * ry);
            x = Math.min(Math.max(0, x), this.rt!.width - 1);
            y = Math.min(Math.max(0, y), this.rt!.height - 1);

            pixel = new Uint8Array(4);
            renderer.readRenderTargetPixels(this.rt!, x, y, 1, 1, pixel);
        } catch (err) {
            console.warn("GPU mesh picking failed:", err);
            pixel = null;
        } finally {
            camera.layers.mask = prevLayers;
            renderer.setRenderTarget(prevTarget);
            renderer.setClearColor(prevClear, prevAlpha);
        }

        if (!pixel) return null;
        const id = pixel[0] + (pixel[1] << 8) + (pixel[2] << 16);
        if (id === 0) return null;
        const entry = this.idToEntry.get(id);
        if (!entry) return null;

        const worldPosition = this.computeWorldPosition(entry.mesh, entry.firstVertexIndex);
        return {mesh: entry.mesh, rangeId: entry.rangeId, worldPosition};
    }
}

export const gpuMeshPicker = new GpuMeshPicker();
