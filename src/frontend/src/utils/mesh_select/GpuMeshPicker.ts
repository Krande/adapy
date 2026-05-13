import * as THREE from "three";
import * as Comlink from "comlink";
import {cameraRef, rendererRef, sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "./CustomBatchedMesh";
import {usePerfStore} from "@/state/perfStore";
// Inline-bundled worker — Vite handles the import + URL plumbing.
// We never instantiate this until the first ``registerMesh`` call so
// startup cost is zero for users who haven't loaded a model yet.
import PickerGeometryWorker from "./pickerGeometry.worker.ts?worker&inline";
import type {
    PickerBuildInput,
    PickerBuildOutput,
    PickerGeometryWorkerAPI,
} from "./pickerGeometry.worker";

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
    /** References to the source mesh's morph BufferAttributes at the
     *  time we duplicated them into the picker. ``applyField`` swaps
     *  these wholesale on every step/scale change (assigning a new
     *  BufferAttribute, not mutating in place), so the references
     *  are the cheap canary for "picker morph is stale, rebuild it"
     *  on the next pick. */
    sourceMorphAttrs: (THREE.BufferAttribute | null)[];
    /** Per-picker-vertex source index — picker vertex ``i`` was
     *  duplicated from original vertex ``sourceVertexIndex[i]``.
     *  Lets us rebuild picker morph attrs on stale-attr detection
     *  without re-walking the index buffer. */
    sourceVertexIndex: Uint32Array;
    /** Total addressable count for the picker geometry's groups —
     *  position-vertex count for non-indexed, index count for the
     *  flat-indexed layout. Cached so the hidden-range sync doesn't
     *  re-query the geometry every pick. */
    pickerTotalCount: number;
    /** Last ``CustomBatchedMesh.hiddenChangeCounter`` value the picker
     *  was synced against. Starts at -1 so the first pick always
     *  rebuilds the groups (covers the freshly-registered case). */
    lastHiddenCounter: number;
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
    private idCounter = 1; // 0 reserved for background

    private idToEntry = new Map<number, IdEntry>();
    private registered = new WeakMap<CustomBatchedMesh, RegisteredMesh>();
    // Meshes whose picker geometry is being built in the worker. Stays
    // in this set from ``registerMesh`` kickoff until the worker's
    // response is installed (or dropped on scene-removal). Prevents
    // both re-registration and accidental pick-time access to a
    // half-built picker. Using a WeakSet means a model-swap that drops
    // the mesh mid-flight doesn't pin it in memory.
    private pending = new WeakSet<CustomBatchedMesh>();

    // Shared invisible material used as slot 1 in every picker mesh's
    // material array. Hidden ranges get materialIndex=1 in their
    // picker geometry group — three's renderer skips draws against an
    // invisible material entirely, so the hidden triangles never
    // rasterize and therefore never write depth. That's the only way
    // a visible element BEHIND a hidden one can win the pick: if
    // hidden triangles were merely tinted background-coloured they'd
    // still occlude in the depth buffer.
    private invisibleMaterial = new THREE.MeshBasicMaterial({visible: false});

    // Lazily-spawned Comlink-wrapped worker. The worker runs the
    // per-triangle position/colour fan-out off the main thread — the
    // biggest single source of registration latency on big FEA meshes.
    private workerApi: Comlink.Remote<PickerGeometryWorkerAPI> | null = null;
    private workerInstance: Worker | null = null;

    // Per-pick scratch — avoids GC churn on the click hot path.
    private _tmpVec2 = new THREE.Vector2();
    private _origProj = new THREE.Matrix4();

    private getWorker(): Comlink.Remote<PickerGeometryWorkerAPI> {
        if (!this.workerApi) {
            this.workerInstance = new PickerGeometryWorker();
            this.workerApi = Comlink.wrap<PickerGeometryWorkerAPI>(this.workerInstance);
        }
        return this.workerApi;
    }

    private ensureRT(_renderer: THREE.WebGLRenderer): void {
        // 1×1 fixed RT. The picker projection (see ``buildPickMatrix``)
        // scales the canvas so a single device pixel at the cursor
        // maps to the entire NDC range, so a 1×1 framebuffer is
        // sufficient to capture the picked pixel. Saves the
        // viewport-sized RT memory (~16 MB at 1080p, ~200 MB at
        // 4K-DPR2) versus the prior full-canvas RT.
        if (!this.rt) {
            this.rt = new THREE.WebGLRenderTarget(1, 1, {
                depthBuffer: true,
                stencilBuffer: false,
                type: THREE.UnsignedByteType,
                format: THREE.RGBAFormat,
            });
        }
    }

    /** Build a "pick matrix" that, when pre-multiplied onto the
     *  camera's projection, restricts the rendered viewport to a
     *  1-pixel window around (clickX, clickY) in canvas device-pixel
     *  coords. Same math as the legacy ``gluPickMatrix(x, y, 1, 1,
     *  viewport)``: translate so the pick pixel's centre lands at
     *  NDC origin, then scale by viewport size so a 1-pixel region
     *  fills the entire NDC range. */
    private buildPickMatrix(
        canvasW: number,
        canvasH: number,
        clickX: number,
        clickY: number,
    ): THREE.Matrix4 {
        const m = new THREE.Matrix4();
        m.set(
            canvasW, 0,       0, canvasW - 2 * clickX,
            0,       canvasH, 0, canvasH - 2 * clickY,
            0,       0,       1, 0,
            0,       0,       0, 1,
        );
        return m;
    }

    private makePickingMaterial(
        morphTargetCount: number,
        flat: boolean,
    ): THREE.ShaderMaterial {
        // pickColor → fragment color. Morph chunks pulled in from
        // three's library so we get the same morph kernel the visible
        // material uses, without having to reimplement the influence
        // blend / morph-texture-vs-attribute decision.
        //
        // Two shader variants:
        //
        //  * GLSL1 (default): interpolates pickColor across the
        //    triangle. Caller MUST guarantee all 3 picker vertices of
        //    a triangle carry the same pickColor (the non-indexed
        //    builder does this by allocating 3 unique picker verts
        //    per triangle).
        //  * GLSL3 + `flat` qualifier: the fragment receives the
        //    provoking vertex's pickColor unchanged, so the picker
        //    geometry can reuse the original shared vertices for two
        //    corners of each triangle and only duplicate the third
        //    (provoking) vertex. Roughly 30-50% less picker memory
        //    on big meshes.
        if (flat) {
            const vertex = `
                precision mediump float;
                #include <common>
                #include <morphtarget_pars_vertex>
                in vec3 pickColor;
                flat out vec3 vPick;
                void main() {
                    vPick = pickColor;
                    vec3 transformed = position;
                    #include <morphtarget_vertex>
                    gl_Position = projectionMatrix * modelViewMatrix * vec4(transformed, 1.0);
                }
            `;
            const fragment = `
                precision mediump float;
                flat in vec3 vPick;
                out vec4 fragColor;
                void main() {
                    fragColor = vec4(vPick, 1.0);
                }
            `;
            const sm = new THREE.ShaderMaterial({
                glslVersion: THREE.GLSL3,
                vertexShader: vertex,
                fragmentShader: fragment,
                side: THREE.DoubleSide,
                depthTest: true,
                depthWrite: true,
            });
            if (morphTargetCount > 0) (sm as any).morphTargets = true;
            return sm;
        }

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

    /** Kick off a picker build for this CustomBatchedMesh. Synchronous
     *  on main: allocates pick IDs, captures source-attr references,
     *  and dispatches the per-triangle position/colour fan-out to a
     *  worker. The picker mesh installs into the scene graph when the
     *  worker responds — until then ``registered`` has no entry and
     *  ``pickAt`` falls through to the raycast path for this mesh.
     *
     *  Idempotent: re-registration short-circuits when already built
     *  or already pending. Geometry mode (flat vs. non-indexed) is
     *  decided here from ``usePerfStore.useFlatPicker`` + the source's
     *  vertex-sharing ratio; later toggle flips take effect on the
     *  next model load. */
    registerMesh(mesh: CustomBatchedMesh): void {
        if (this.registered.has(mesh) || this.pending.has(mesh)) return;
        const geom = mesh.geometry as THREE.BufferGeometry;
        if (!geom.index || !geom.attributes.position) return;
        if (mesh.drawRanges.size === 0) return;

        const t0 = performance.now();
        const indices = geom.index.array as Uint16Array | Uint32Array;
        const posAttr = geom.attributes.position as THREE.BufferAttribute;
        const posArr = posAttr.array as Float32Array;
        const itemSize = posAttr.itemSize; // typically 3
        const nTris = (geom.index.count / 3) | 0;

        // Per-triangle colour map + global id allocation. ID assignment
        // MUST happen on main (sequential counter shared across all
        // registered meshes); the worker only consumes the resulting
        // ``triColor`` array, not the counter. Adding to ``idToEntry``
        // here is also safe — until the worker responds, the picker
        // mesh isn't in the scene, so no GPU pixel can decode to an id
        // we haven't yet built geometry for.
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

        const sourceMorphs = geom.morphAttributes?.position as
            | THREE.BufferAttribute[]
            | undefined;
        const morphTargetCount = sourceMorphs?.length ?? 0;
        const morphItemSize = sourceMorphs?.[0]?.itemSize ?? 3;

        // The flat-picker toggle is a *preference*, not a force.
        // Whether flat actually saves memory depends on the source
        // mesh's vertex-sharing ratio α = nOrigVerts / nTris:
        //
        //   * Flat picker:        (27α + 39) bytes/tri + morph
        //   * Non-indexed picker:        81  bytes/tri + morph
        //
        // Flat is smaller only when α < 1.556. CAD models with merged
        // primitives often hit α≈1, where flat saves ~30-40%; FEA
        // bakes typically emit one vertex set per element (α≈3),
        // where flat is ~50% **bigger** because of the extra index
        // buffer + per-source-vertex pickColor copies (and Three's
        // morph texture amplifies both layouts by ~2×). Auto-pick the
        // cheaper layout so a user enabling the toggle on the
        // Performance panel never gets a regression.
        const wantFlat = usePerfStore.getState().useFlatPicker;
        const flat = wantFlat && this.flatIsCheaper(
            posAttr.count, nTris, morphTargetCount,
        );

        if (wantFlat && !flat) {
            const alpha = posAttr.count / nTris;
            console.info(
                `[GpuMeshPicker] flat-picker disabled for "${mesh.name}": ` +
                `vertex-sharing α=${alpha.toFixed(2)} ≥ 1.556 — ` +
                `non-indexed picker is cheaper for this mesh`,
            );
        }

        // Slice the source typed arrays so we can transfer the copies
        // into the worker without detaching the BufferAttribute-owned
        // buffers the visible mesh is still rendering from. Typed-array
        // slice is a single bulk memcpy in the engine — ~2 ms for a
        // 36 MB index buffer on commodity x86, dwarfed by the 50–200 ms
        // of per-triangle work the worker takes off main.
        const indicesCopy = indices.slice();
        const posCopy = posArr.slice();
        const morphCopies: Float32Array[] = [];
        for (let m = 0; m < morphTargetCount; m++) {
            morphCopies.push((sourceMorphs![m].array as Float32Array).slice());
        }

        const input: PickerBuildInput = {
            flat,
            indices: indicesCopy,
            posArr: posCopy,
            itemSize,
            nTris,
            nOrigVerts: posAttr.count,
            triColor,
            morphArrs: morphCopies,
            morphItemSize,
            morphTargetsRelative: geom.morphTargetsRelative === true,
        };
        // ``.buffer`` types as ``ArrayBufferLike`` (includes
        // SharedArrayBuffer); we always allocate plain ArrayBuffers
        // here so the cast is safe.
        const transfers: ArrayBuffer[] = [
            indicesCopy.buffer as ArrayBuffer,
            posCopy.buffer as ArrayBuffer,
            triColor.buffer as ArrayBuffer,
            ...morphCopies.map((m) => m.buffer as ArrayBuffer),
        ];

        this.pending.add(mesh);
        // Capture sourceMorphs by reference so the install handler can
        // wire ``sourceMorphAttrs`` for staleness detection. Closing
        // over these doesn't keep the mesh alive on its own — the
        // CustomBatchedMesh holds the morph attrs anyway.
        void this.runWorkerBuild(mesh, input, transfers, sourceMorphs, flat, morphTargetCount, t0);
    }

    /** Worker-side build + main-side install. Split out so
     *  ``registerMesh`` stays synchronous for callers. */
    private async runWorkerBuild(
        mesh: CustomBatchedMesh,
        input: PickerBuildInput,
        transfers: ArrayBuffer[],
        sourceMorphs: THREE.BufferAttribute[] | undefined,
        flat: boolean,
        morphTargetCount: number,
        t0: number,
    ): Promise<void> {
        let out: PickerBuildOutput;
        try {
            const worker = this.getWorker();
            out = await worker.build(Comlink.transfer(input, transfers));
        } catch (err) {
            this.pending.delete(mesh);
            console.warn(`[GpuMeshPicker] worker build failed for "${mesh.name}":`, err);
            return;
        }

        // Note: we deliberately do NOT check ``hasAncestor(mesh, scene)``
        // here. Eager registration calls ``registerMesh`` from the
        // factory, before the mesh is attached anywhere — the guard
        // would discard every eager build. If the mesh truly got
        // dropped mid-flight, the picker attaches to a detached parent,
        // never renders (picker layer only enables during pickAt, and
        // the parent's matrixWorld never propagates), and the cleanup
        // in ``sweepOrphaned`` collects it on the next pick.

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(out.positions, 3));
        geometry.setAttribute("pickColor", new THREE.BufferAttribute(out.colors, 3, true));
        if (out.indices) geometry.setIndex(new THREE.BufferAttribute(out.indices, 1));

        const sourceMorphAttrs: (THREE.BufferAttribute | null)[] = [];
        if (out.morphArrs.length > 0 && sourceMorphs) {
            geometry.morphAttributes.position = [];
            for (let m = 0; m < out.morphArrs.length; m++) {
                geometry.morphAttributes.position.push(
                    new THREE.BufferAttribute(out.morphArrs[m], 3),
                );
                sourceMorphAttrs.push(sourceMorphs[m] ?? null);
            }
            geometry.morphTargetsRelative = out.morphTargetsRelative;
        }

        // Material is always an array — slot 0 is the picker shader,
        // slot 1 is the shared invisible material used to hide ranges
        // from the pick render (see ``syncHiddenGroups``). ``pickAt``
        // seeds a single group covering the whole geometry with
        // materialIndex=0 on the first sync, so the unhidden case is
        // still one draw call.
        const pickerMesh = new THREE.Mesh(
            geometry,
            [this.makePickingMaterial(morphTargetCount, flat), this.invisibleMaterial],
        );
        pickerMesh.name = `__pick__${mesh.name}`;
        pickerMesh.frustumCulled = mesh.frustumCulled;
        pickerMesh.layers.set(PICK_LAYER);
        mesh.add(pickerMesh);

        // Picker-geometry total addressable count for groups:
        //   * non-indexed → position-vertex count (3 verts per tri)
        //   * flat-indexed → index count
        const pickerTotalCount = geometry.index
            ? geometry.index.count
            : (geometry.getAttribute("position") as THREE.BufferAttribute).count;

        this.registered.set(mesh, {
            pickerMesh,
            morphTargetCount,
            sourceMorphAttrs,
            sourceVertexIndex: out.sourceVertexIndex,
            pickerTotalCount,
            lastHiddenCounter: -1,
        });
        this.pending.delete(mesh);

        const memMB = out.byteLength / 1024 / 1024;
        const dt = performance.now() - t0;
        console.info(
            `[GpuMeshPicker] built ${flat ? "flat" : "non-indexed"} picker for ` +
            `"${mesh.name}" in ${dt.toFixed(1)}ms ` +
            `(${input.nTris} tris, ${morphTargetCount} morph(s), ` +
            `~${memMB.toFixed(1)}MB, off-thread)`,
        );
    }

    /** Decide whether the flat-varying builder produces a strictly
     *  smaller picker than the non-indexed builder, given the source
     *  mesh's shape. Per-tri cost (in CPU bytes; GPU is similar):
     *
     *    non-indexed = 3·(12 + 3 + 12·morphCount)
     *    flat        = (nOrig/nTris)·(12 + 3 + 12·morphCount)
     *                  + 12·1 (index)
     *                  + 1·(12 + 3 + 12·morphCount) (provoking vert)
     *
     *  Solving for the per-vertex bytes ``vb = 15 + 12·m``:
     *    flat < non-indexed  ⇔  vb·(α + 1) + 12  <  3·vb
     *                       ⇔  α  <  2 − 12/vb
     *
     *  For m=0: α < 2 − 12/15 = 1.2
     *  For m=1: α < 2 − 12/27 ≈ 1.555
     *  For m=2: α < 2 − 12/39 ≈ 1.692
     *
     *  Returns true when the source's vertex-sharing ratio crosses
     *  the threshold for the current morph count. */
    private flatIsCheaper(
        nOrigVerts: number,
        nTris: number,
        morphCount: number,
    ): boolean {
        if (nTris === 0) return false;
        const vb = 15 + 12 * morphCount;
        const threshold = 2 - 12 / vb;
        const alpha = nOrigVerts / nTris;
        return alpha < threshold;
    }

    /** Duplicate a source morph BufferAttribute into the picker's
     *  non-indexed layout. Pulled out so refreshMorphIfStale and the
     *  initial registration share the same fan-out kernel. */
    private duplicateMorphAttr(
        src: THREE.BufferAttribute,
        sourceVertexIndex: Uint32Array,
    ): Float32Array {
        const srcArr = src.array as Float32Array;
        const srcItem = src.itemSize; // typically 3
        const nPickerVerts = sourceVertexIndex.length;
        const dup = new Float32Array(nPickerVerts * 3);
        for (let p = 0; p < nPickerVerts; p++) {
            const s = sourceVertexIndex[p] * srcItem;
            const o = p * 3;
            dup[o + 0] = srcArr[s];
            dup[o + 1] = srcArr[s + 1];
            dup[o + 2] = srcArr[s + 2];
        }
        return dup;
    }

    /** Tear down a registered picker mesh and reclaim its GPU buffers
     *  + idToEntry slots. Used by sweepOrphaned and by the morph-count
     *  rebuild path. Doesn't touch the source mesh otherwise. */
    private disposePicker(mesh: CustomBatchedMesh, reg: RegisteredMesh): void {
        const pm = reg.pickerMesh;
        if (pm.parent) pm.parent.remove(pm);
        (pm.geometry as THREE.BufferGeometry).dispose();
        const m = pm.material as THREE.Material | THREE.Material[];
        // The invisible material is shared across all picker meshes,
        // never dispose it. Other materials are per-picker shader
        // instances and ours to release.
        if (Array.isArray(m)) {
            for (const x of m) if (x !== this.invisibleMaterial) x.dispose();
        } else if (m !== this.invisibleMaterial) {
            m.dispose();
        }
        this.registered.delete(mesh);
        // Drop the idToEntry rows for this mesh — a re-registration
        // re-allocates ids from the global counter. Old ids would
        // never decode again anyway (no picker geometry writes them).
        for (const [id, entry] of this.idToEntry) {
            if (entry.mesh === mesh) this.idToEntry.delete(id);
        }
    }

    /** Detect a source-morph attribute swap or count change and
     *  refresh the picker:
     *
     *   * Same morph count, swapped BufferAttributes (applyField path):
     *     rebuild the picker's duplicated deltas in place, reusing the
     *     picker BufferAttribute so three's WebGLAttributes re-uploads
     *     to the same GPU buffer. Cheap.
     *   * Count changed (FEA streaming wiring morphs after registration):
     *     dispose the picker entirely and kick off a fresh worker build.
     *     The shader was compiled for the original morph count, so an
     *     in-place fix isn't possible. The first pick after this falls
     *     through to raycast for the affected mesh; subsequent picks
     *     hit the rebuilt picker.
     *
     *  Returns true if any in-place refresh happened. A count-change
     *  rebuild also returns true (the caller's view of "needs work
     *  this frame" is satisfied). */
    private refreshMorphIfStale(
        mesh: CustomBatchedMesh,
        reg: RegisteredMesh,
    ): boolean {
        const srcMorphs = (mesh.geometry as THREE.BufferGeometry)
            .morphAttributes?.position as THREE.BufferAttribute[] | undefined;
        const srcMorphCount = srcMorphs?.length ?? 0;

        if (srcMorphCount !== reg.morphTargetCount) {
            // Morph slot count changed — usually because the mesh was
            // eager-registered at construction (no morphs known) and
            // a streaming load wired morphs in afterwards. Rebuild
            // from scratch via the worker; ``registerMesh`` is
            // idempotent and the fresh build will see the new morph
            // attrs.
            this.disposePicker(mesh, reg);
            this.registerMesh(mesh);
            return true;
        }

        if (reg.morphTargetCount === 0) return false;
        if (!srcMorphs || srcMorphs.length === 0) return false;

        const pickerMorphs = (reg.pickerMesh.geometry as THREE.BufferGeometry)
            .morphAttributes.position as THREE.BufferAttribute[] | undefined;
        if (!pickerMorphs || pickerMorphs.length !== srcMorphs.length) {
            // Defensive — picker's own morph slots mis-wired. Same
            // remedy as a source-count change.
            this.disposePicker(mesh, reg);
            this.registerMesh(mesh);
            return true;
        }

        let changed = false;
        for (let m = 0; m < srcMorphs.length; m++) {
            if (reg.sourceMorphAttrs[m] === srcMorphs[m]) continue;
            // applyField swapped this attribute — rebuild.
            const dup = this.duplicateMorphAttr(srcMorphs[m], reg.sourceVertexIndex);
            const pickerAttr = pickerMorphs[m];
            // Reuse the picker BufferAttribute by swapping its array
            // — three.js's WebGLAttributes will see needsUpdate and
            // re-upload to the same GPU buffer.
            (pickerAttr as any).array = dup;
            pickerAttr.needsUpdate = true;
            reg.sourceMorphAttrs[m] = srcMorphs[m];
            changed = true;
        }
        if (changed) {
            // morphTargetsRelative may have flipped too (unlikely but
            // cheap to keep in sync).
            (reg.pickerMesh.geometry as THREE.BufferGeometry).morphTargetsRelative =
                (mesh.geometry as THREE.BufferGeometry).morphTargetsRelative === true;
            // Force three to rebuild the morph texture against the
            // updated picker attribute on next render.
            (reg.pickerMesh.geometry as THREE.BufferGeometry).dispatchEvent({type: "dispose"});
        }
        return changed;
    }

    /** Mirror the source mesh's hidden-range set into the picker
     *  geometry's groups so hidden triangles render against the
     *  invisible material (slot 1) and never reach the pick framebuffer.
     *
     *  Without this, shift+H would mark elements invisible on the
     *  visible mesh but the picker would happily return them — and
     *  worse, their depth would block clicks on visible elements behind.
     *
     *  Cheap: ``hiddenChangeCounter`` lets us early-out on every pick
     *  where nothing has changed. Group rebuilds only run on the click
     *  immediately after a hide/unhide. */
    private syncHiddenGroups(mesh: CustomBatchedMesh, reg: RegisteredMesh): void {
        if (mesh.hiddenChangeCounter === reg.lastHiddenCounter) return;
        reg.lastHiddenCounter = mesh.hiddenChangeCounter;

        const pickerGeom = reg.pickerMesh.geometry as THREE.BufferGeometry;
        const total = reg.pickerTotalCount;
        pickerGeom.clearGroups();

        const hidden = mesh.getHiddenRanges();
        if (hidden.size === 0) {
            // Fast path: one group, one draw call, materialIndex=0.
            pickerGeom.addGroup(0, total, 0);
            return;
        }

        // Walk drawRanges in start order, emit groups. Coalesce runs
        // of visible ranges (and gaps between drawRanges) into single
        // materialIndex=0 groups; each hidden range gets its own
        // materialIndex=1 group. Mirrors CustomBatchedMesh.updateGroups
        // and reuses the same cached sorted-segments view so we don't
        // re-sort drawRanges on every pick.
        const segs = mesh.getSortedSegments();
        const n = segs.ids.length;

        let cur = 0;
        let runStart: number | null = null;
        const flushRun = (end: number) => {
            if (runStart !== null && end > runStart) {
                pickerGeom.addGroup(runStart, end - runStart, 0);
            }
            runStart = null;
        };
        for (let i = 0; i < n; i++) {
            const id = segs.ids[i];
            const s = segs.starts[i];
            const c = segs.counts[i];
            if (s > cur && runStart === null) runStart = cur;
            if (hidden.has(id)) {
                flushRun(s);
                pickerGeom.addGroup(s, c, 1);
            } else {
                if (runStart === null) runStart = s;
            }
            cur = s + c;
        }
        if (cur < total && runStart === null) runStart = cur;
        flushRun(total);
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

    /** Returns true iff ``obj`` has ``ancestor`` somewhere up the
     *  ``.parent`` chain. Used to detect picker meshes whose source
     *  CustomBatchedMesh has been removed from the active scene
     *  (model swap, etc.) — those are leaks we need to dispose. */
    private hasAncestor(obj: THREE.Object3D, ancestor: THREE.Object3D): boolean {
        let cur: THREE.Object3D | null = obj;
        while (cur) {
            if (cur === ancestor) return true;
            cur = cur.parent;
        }
        return false;
    }

    /** Walk ``idToEntry`` and drop entries whose source mesh is no
     *  longer rooted in the active scene. Disposes the picker mesh's
     *  geometry + material so the GPU buffers are released.
     *
     *  Without this sweep, ``idToEntry`` holds strong references to
     *  every registered CustomBatchedMesh forever. Replacing the
     *  loaded model (a common interaction) leaves the old mesh +
     *  its picker child stuck in GPU memory; over a few swaps the
     *  picker memory accumulates rather than churning. */
    private sweepOrphaned(scene: THREE.Scene): void {
        const deadIds: number[] = [];
        // Track which meshes we've already inspected so we don't
        // walk the parent chain N times per mesh (idToEntry has one
        // entry per drawRange per mesh).
        const meshAlive = new Map<CustomBatchedMesh, boolean>();
        for (const [id, entry] of this.idToEntry) {
            let alive = meshAlive.get(entry.mesh);
            if (alive === undefined) {
                alive = this.hasAncestor(entry.mesh, scene);
                meshAlive.set(entry.mesh, alive);
            }
            if (!alive) deadIds.push(id);
        }
        if (deadIds.length === 0) return;

        for (const [mesh, alive] of meshAlive) {
            if (alive) continue;
            const reg = this.registered.get(mesh);
            if (reg) {
                const pm = reg.pickerMesh;
                if (pm.parent) pm.parent.remove(pm);
                (pm.geometry as THREE.BufferGeometry).dispose();
                const m = pm.material as THREE.Material | THREE.Material[];
                if (Array.isArray(m)) m.forEach((x) => x.dispose());
                else m.dispose();
            }
            this.registered.delete(mesh);
        }
        for (const id of deadIds) this.idToEntry.delete(id);
    }

    /** Run a GPU pick at the given client coordinates. Returns the
     *  picked mesh + rangeId, or null on miss / morph-active. */
    pickAt(clientX: number, clientY: number): GpuMeshPickResult {
        const renderer = rendererRef.current;
        const scene = sceneRef.current;
        const camera = cameraRef.current as THREE.PerspectiveCamera | null;
        if (!renderer || !scene || !camera) return null;

        // Reclaim GPU memory for any picker whose source mesh has
        // been swapped out of the scene. Cheap when no swaps happened.
        this.sweepOrphaned(scene);

        // Lazy registration: any CustomBatchedMesh in the scene that
        // isn't yet built gets its picker mesh now. First click pays
        // the cost; later clicks are free. While walking, also:
        //   1. Re-link the picker's ``morphTargetInfluences`` to the
        //      source array by reference, so a slider scrub that
        //      mutates srcInfl[0] is visible to the picker without
        //      any further work.
        //   2. Detect a source-morph attribute swap (applyField
        //      reassigns the position morph attrs on every step or
        //      re-apply) and rebuild the picker's duplicated deltas
        //      in place. Without this the picker renders an older
        //      step's deformation and clicks land in the wrong pixel.
        scene.traverse((o) => {
            if (!(o instanceof CustomBatchedMesh)) return;
            this.registerMesh(o);
            const reg = this.registered.get(o);
            if (!reg) return;
            // Re-sync picker groups against the source's hidden set
            // before the morph branch — hidden-range mutation is
            // independent of morph state, and a freshly-registered
            // mesh needs its initial group emitted regardless of
            // whether it has morphs.
            this.syncHiddenGroups(o, reg);
            // Always call refreshMorphIfStale, even when the registered
            // picker has zero morph slots: an eager-registered FEA mesh
            // starts with morphTargetCount=0, gets morphs wired later
            // by applyWarp, and this is the path that detects the
            // count change and triggers a worker rebuild. The function
            // is cheap when nothing changed.
            const rebuilt = this.refreshMorphIfStale(o, reg);
            if (rebuilt && !this.registered.has(o)) {
                // Picker was disposed for a count-change rebuild —
                // ``reg`` is stale, skip the influence/dict relink.
                return;
            }
            if (reg.morphTargetCount === 0) return;
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

        // Pick-matrix: rewrite the camera's projection so the click
        // pixel is the only thing that lands inside the 1×1 RT. Math
        // operates in canvas device pixels with Y-up so it matches
        // WebGL's framebuffer orientation. We save + restore the
        // camera's projectionMatrix; projectionMatrixInverse is only
        // used outside the render path so it's safe to leave stale
        // for the duration of one render.
        const drawBuf = this._tmpVec2;
        renderer.getDrawingBufferSize(drawBuf);
        const canvasW = drawBuf.x;
        const canvasH = drawBuf.y;
        const dpr = renderer.getPixelRatio();
        const clickX = (clientX - rect.left) * dpr;
        const clickY = (rect.bottom - clientY) * dpr;
        const pickMat = this.buildPickMatrix(canvasW, canvasH, clickX, clickY);
        const origProj = this._origProj.copy(camera.projectionMatrix);
        camera.projectionMatrix.premultiply(pickMat);

        let pixel: Uint8Array | null = null;
        try {
            renderer.setRenderTarget(this.rt);
            renderer.setClearColor(0x000000, 0);
            renderer.clear();
            renderer.render(scene, camera);

            // Always pixel (0, 0): the pickMatrix steered the entire
            // 1×1 viewport to the cursor's content.
            pixel = new Uint8Array(4);
            renderer.readRenderTargetPixels(this.rt!, 0, 0, 1, 1, pixel);
        } catch (err) {
            console.warn("GPU mesh picking failed:", err);
            pixel = null;
        } finally {
            camera.layers.mask = prevLayers;
            camera.projectionMatrix.copy(origProj);
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
