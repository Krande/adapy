import * as THREE from "three";
import {cameraRef, rendererRef, sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "./CustomBatchedMesh";

// GPU face-picker for CustomBatchedMesh, mirroring GpuPointPicker.
// Each mesh gets a per-vertex ``pickColor`` attribute that encodes a
// 24-bit id derived from its drawRange. A parallel ShaderMaterial
// renders that color directly. On a click we render the scene with
// the picker materials swapped in, read one pixel, decode the id back
// to (mesh, rangeId), and look up a representative world position.
//
// Trade-offs vs the CPU raycast path:
//
//   * O(1) regardless of triangle count — replaces a linear scan over
//     millions of triangles with a 1×1 pixel readback. Selection on a
//     3M-tri mobile model drops from ~800ms to ~3ms.
//   * Morph-aware for free — the GPU draws the deformed geometry, so
//     scrubbing the FEA slider doesn't break picks (the BVH path we
//     considered would have fallen back to CPU under morph).
//   * Hidden ranges are skipped natively: the picker material array
//     mirrors CustomBatchedMesh's three slots (picker, picker,
//     invisible), so groups bound to slot 2 don't render and aren't
//     pickable.
//
// Boundary ambiguity: vertices shared between adjacent drawRanges
// take whichever range was painted last. A click on the exact
// boundary pixel might resolve to either neighbor — acceptable UX
// for finger taps on mobile, and visually invisible at typical
// element sizes.

export type GpuMeshPickResult = {
    mesh: CustomBatchedMesh;
    rangeId: string;
    worldPosition: THREE.Vector3;
} | null;

interface RegisteredMesh {
    pickMat: THREE.ShaderMaterial;
    invisibleMat: THREE.MeshBasicMaterial;
    originalMaterial: THREE.Material | THREE.Material[];
}

interface IdEntry {
    mesh: CustomBatchedMesh;
    rangeId: string;
    /** First vertex index referenced by this range — used as a stable
     *  point for computing the picked element's world position
     *  (centroid would need an extra pass; first-vertex is one
     *  attribute lookup + optional morph apply). */
    firstVertexIndex: number;
}

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

    private makePickingMaterial(mesh: CustomBatchedMesh): THREE.ShaderMaterial {
        // Minimal vertex/fragment pair: pass the per-vertex pickColor
        // attribute straight to the fragment. Three's
        // ``morphtarget_pars_vertex`` / ``morphtarget_vertex`` chunks
        // bring in the same shader code the visible material uses, so
        // the picker tracks deformation in lockstep without a custom
        // morph kernel.
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
            // Match the visible material's side handling so a click on
            // a back-facing triangle of a DoubleSide mesh still picks.
            side: THREE.DoubleSide,
            depthTest: true,
            depthWrite: true,
        });
        const g = mesh.geometry as THREE.BufferGeometry;
        const hasMorphs = !!g.morphAttributes?.position?.length;
        if (hasMorphs) (sm as any).morphTargets = true;
        return sm;
    }

    /** Build the per-vertex ``pickColor`` attribute + picking material
     *  for this mesh. Idempotent — re-registration short-circuits.
     *
     *  Cost: O(sum of range index counts) ≈ O(n_triangles) once per
     *  mesh load. On a 3M-tri model this is ~50–150ms on mobile;
     *  callers should defer registration until the first pick to keep
     *  it off the critical model-load path. */
    registerMesh(mesh: CustomBatchedMesh): void {
        if (this.registered.has(mesh)) return;
        const geom = mesh.geometry as THREE.BufferGeometry;
        if (!geom.index || !geom.attributes.position) return;
        if (mesh.drawRanges.size === 0) return;

        const t0 = performance.now();
        const vertexCount = (geom.attributes.position as THREE.BufferAttribute).count;
        const colors = new Float32Array(vertexCount * 3);
        const indices = geom.index.array as Uint16Array | Uint32Array;

        // Walk each drawRange, assign a fresh global id, paint its
        // triangles' three vertices with the encoded RGB. Vertices
        // shared between adjacent ranges get the colour of whichever
        // range is processed last — the boundary ambiguity is fine
        // for finger taps and visually invisible at typical element
        // sizes.
        for (const [rangeId, [start, count]] of mesh.drawRanges) {
            if (count <= 0) continue;
            const id = this.idCounter++;
            const r = (id & 0xff) / 255;
            const g = ((id >> 8) & 0xff) / 255;
            const b = ((id >> 16) & 0xff) / 255;
            const firstVertexIndex = indices[start];
            this.idToEntry.set(id, {mesh, rangeId, firstVertexIndex});
            for (let i = start; i < start + count; i++) {
                const vi = indices[i] * 3;
                colors[vi] = r;
                colors[vi + 1] = g;
                colors[vi + 2] = b;
            }
        }

        geom.setAttribute("pickColor", new THREE.BufferAttribute(colors, 3, false));

        const pickMat = this.makePickingMaterial(mesh);
        // Slot 2 in CustomBatchedMesh's material array is the
        // "invisible" overlay used for hidden ranges. Mirror that in
        // the picker so hidden ranges don't render → not pickable.
        const invisibleMat = new THREE.MeshBasicMaterial({visible: false});
        this.registered.set(mesh, {
            pickMat,
            invisibleMat,
            originalMaterial: mesh.material,
        });

        const dt = performance.now() - t0;
        console.info(
            `[GpuMeshPicker] registered "${mesh.name}" in ${dt.toFixed(1)}ms ` +
            `(${mesh.drawRanges.size} ranges, ${vertexCount} verts)`,
        );
    }

    /** Compute the world-space position of the picked range's first
     *  vertex, applying CPU morph blending so deformed picks report a
     *  coordinate on the actual rendered surface (not the un-deformed
     *  base). One vertex per pick — microseconds. */
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
     *  picked mesh + rangeId, or null on miss. */
    pickAt(clientX: number, clientY: number): GpuMeshPickResult {
        const renderer = rendererRef.current;
        const scene = sceneRef.current;
        const camera = cameraRef.current as THREE.PerspectiveCamera | null;
        if (!renderer || !scene || !camera) return null;

        // Lazy registration: any CustomBatchedMesh in the scene that
        // isn't yet registered gets its pickColor attribute + picker
        // material built now. First pick pays the cost; later picks
        // are free.
        scene.traverse((o) => {
            if (o instanceof CustomBatchedMesh) {
                this.registerMesh(o);
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

        // Swap each registered mesh's material array to
        // [pickMat, pickMat, invisibleMat]. Slots match the
        // CustomBatchedMesh layout so groups bound to materialIndex 2
        // (hidden ranges) stay hidden, and slots 0/1 both render the
        // picker. Restore in the finally.
        const toRestore: Array<{mesh: CustomBatchedMesh; orig: THREE.Material | THREE.Material[]}> = [];
        scene.traverse((o) => {
            if (!(o instanceof CustomBatchedMesh)) return;
            const reg = this.registered.get(o);
            if (!reg) return;
            toRestore.push({mesh: o, orig: o.material});
            o.material = [reg.pickMat, reg.pickMat, reg.invisibleMat];
        });

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
            for (const {mesh, orig} of toRestore) mesh.material = orig;
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
