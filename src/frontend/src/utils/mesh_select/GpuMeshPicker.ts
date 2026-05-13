import * as THREE from "three";
import {cameraRef, rendererRef, sceneRef} from "@/state/refs";
import {CustomBatchedMesh} from "./CustomBatchedMesh";
import {usePerfStore} from "@/state/perfStore";

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

    // Shared invisible material used as slot 1 in every picker mesh's
    // material array. Hidden ranges get materialIndex=1 in their
    // picker geometry group — three's renderer skips draws against an
    // invisible material entirely, so the hidden triangles never
    // rasterize and therefore never write depth. That's the only way
    // a visible element BEHIND a hidden one can win the pick: if
    // hidden triangles were merely tinted background-coloured they'd
    // still occlude in the depth buffer.
    private invisibleMaterial = new THREE.MeshBasicMaterial({visible: false});

    // Per-pick scratch — avoids GC churn on the click hot path.
    private _tmpVec2 = new THREE.Vector2();
    private _origProj = new THREE.Matrix4();

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

    /** Build the picker child mesh for this CustomBatchedMesh.
     *  Idempotent — re-registration short-circuits. Geometry mode is
     *  decided at FIRST registration by ``usePerfStore.useFlatPicker``;
     *  later toggle flips take effect on the next model load. */
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

        // Per-triangle colour map + global id allocation. Walking the
        // drawRanges here means both the non-indexed and flat builders
        // get the same id mapping; only the geometry layout differs.
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

        const built = flat
            ? this.buildFlatPickerGeometry(
                indices, posArr, itemSize, nTris, posAttr.count, triColor,
                sourceMorphs, geom.morphTargetsRelative === true,
            )
            : this.buildNonIndexedPickerGeometry(
                indices, posArr, itemSize, nTris, triColor,
                sourceMorphs, geom.morphTargetsRelative === true,
            );

        if (wantFlat && !flat) {
            // Make the fallback observable so users don't think the
            // toggle is broken. One-line info per mesh, runs once
            // per mesh per session.
            const alpha = posAttr.count / nTris;
            console.info(
                `[GpuMeshPicker] flat-picker disabled for "${mesh.name}": ` +
                `vertex-sharing α=${alpha.toFixed(2)} ≥ 1.556 — ` +
                `non-indexed picker is cheaper for this mesh`,
            );
        }

        // Material is always an array — slot 0 is the picker shader,
        // slot 1 is the shared invisible material used to hide ranges
        // from the pick render (see ``syncHiddenGroups``). ``pickAt``
        // seeds a single group covering the whole geometry with
        // materialIndex=0 on the first sync, so the unhidden case is
        // still one draw call.
        const pickerMesh = new THREE.Mesh(
            built.geometry,
            [this.makePickingMaterial(morphTargetCount, flat), this.invisibleMaterial],
        );
        pickerMesh.name = `__pick__${mesh.name}`;
        pickerMesh.frustumCulled = mesh.frustumCulled;
        pickerMesh.layers.set(PICK_LAYER);
        // Child of the original mesh so it inherits matrixWorld
        // automatically — pickerMesh moves with its parent on any
        // scene-graph transform update.
        mesh.add(pickerMesh);

        // Picker-geometry total addressable count for groups:
        //   * non-indexed → position-vertex count (3 verts per tri)
        //   * flat-indexed → index count
        // Both happen to equal the original drawRange's address space,
        // so the group sync below can use drawRanges' start/count
        // directly without remapping.
        const pickerTotalCount = built.geometry.index
            ? built.geometry.index.count
            : (built.geometry.getAttribute("position") as THREE.BufferAttribute).count;

        this.registered.set(mesh, {
            pickerMesh,
            morphTargetCount,
            sourceMorphAttrs: built.sourceMorphAttrs,
            sourceVertexIndex: built.sourceVertexIndex,
            pickerTotalCount,
            lastHiddenCounter: -1,
        });

        const memMB = built.byteLength / 1024 / 1024;
        const dt = performance.now() - t0;
        console.info(
            `[GpuMeshPicker] built ${flat ? "flat" : "non-indexed"} picker for ` +
            `"${mesh.name}" in ${dt.toFixed(1)}ms ` +
            `(${nTris} tris, ${morphTargetCount} morph(s), ~${memMB.toFixed(1)}MB)`,
        );
    }

    /** Non-indexed: 3 unique picker vertices per triangle, all with
     *  the same pickColor. Standard varying interpolates to that
     *  constant. Simple and works on any WebGL version, but uses ~3×
     *  the position memory of a shared-vertex layout. */
    private buildNonIndexedPickerGeometry(
        indices: Uint16Array | Uint32Array,
        posArr: Float32Array,
        itemSize: number,
        nTris: number,
        triColor: Uint8Array,
        sourceMorphs: THREE.BufferAttribute[] | undefined,
        morphTargetsRelative: boolean,
    ): {
        geometry: THREE.BufferGeometry;
        sourceVertexIndex: Uint32Array;
        sourceMorphAttrs: (THREE.BufferAttribute | null)[];
        byteLength: number;
    } {
        const pickerPositions = new Float32Array(nTris * 9);
        const pickerColors = new Uint8Array(nTris * 9);
        const sourceVertexIndex = new Uint32Array(nTris * 3);
        for (let ti = 0; ti < nTris; ti++) {
            const v0 = indices[ti * 3];
            const v1 = indices[ti * 3 + 1];
            const v2 = indices[ti * 3 + 2];
            const i0 = v0 * itemSize;
            const i1 = v1 * itemSize;
            const i2 = v2 * itemSize;
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

            sourceVertexIndex[ti * 3 + 0] = v0;
            sourceVertexIndex[ti * 3 + 1] = v1;
            sourceVertexIndex[ti * 3 + 2] = v2;

            const r = triColor[ti * 3];
            const g = triColor[ti * 3 + 1];
            const b = triColor[ti * 3 + 2];
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

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(pickerPositions, 3));
        geometry.setAttribute("pickColor", new THREE.BufferAttribute(pickerColors, 3, true));

        const sourceMorphAttrs: (THREE.BufferAttribute | null)[] = [];
        let morphMemBytes = 0;
        if (sourceMorphs && sourceMorphs.length > 0) {
            geometry.morphAttributes.position = [];
            for (let m = 0; m < sourceMorphs.length; m++) {
                const src = sourceMorphs[m];
                const dup = this.duplicateMorphAttr(src, sourceVertexIndex);
                geometry.morphAttributes.position.push(new THREE.BufferAttribute(dup, 3));
                sourceMorphAttrs.push(src);
                morphMemBytes += dup.byteLength;
            }
            geometry.morphTargetsRelative = morphTargetsRelative;
        }

        return {
            geometry,
            sourceVertexIndex,
            sourceMorphAttrs,
            byteLength: pickerPositions.byteLength + pickerColors.byteLength + morphMemBytes,
        };
    }

    /** Flat-varying: indexed geometry where each triangle reuses the
     *  ORIGINAL mesh's two leading vertex indices and only the third
     *  (provoking) vertex is duplicated. Combined with a GLSL3 `flat`
     *  varying, the fragment shader takes the provoking vertex's
     *  pickColor unchanged for the whole triangle — no interpolation,
     *  no shared-vertex colour conflict, and roughly 30–50% less
     *  picker memory vs the non-indexed layout.
     *
     *  Picker vertex layout: ``[ ...orig_verts, ...extras ]`` where
     *  ``extras[ti]`` is a duplicate of the original mesh's third
     *  vertex of triangle ``ti``. Index buffer rewrites each
     *  triangle (a, b, c) as (a, b, nOrigVerts + ti). The
     *  WebGL2 provoking-vertex convention is LAST_VERTEX_CONVENTION
     *  (the default in WebGL2 / OpenGL ES 3.0), so the duplicated
     *  vertex is what the rasterizer takes the flat colour from. */
    private buildFlatPickerGeometry(
        indices: Uint16Array | Uint32Array,
        posArr: Float32Array,
        itemSize: number,
        nTris: number,
        nOrigVerts: number,
        triColor: Uint8Array,
        sourceMorphs: THREE.BufferAttribute[] | undefined,
        morphTargetsRelative: boolean,
    ): {
        geometry: THREE.BufferGeometry;
        sourceVertexIndex: Uint32Array;
        sourceMorphAttrs: (THREE.BufferAttribute | null)[];
        byteLength: number;
    } {
        const nPickerVerts = nOrigVerts + nTris;
        const pickerPositions = new Float32Array(nPickerVerts * 3);
        const pickerColors = new Uint8Array(nPickerVerts * 3);
        const pickerIndices = new Uint32Array(nTris * 3);
        const sourceVertexIndex = new Uint32Array(nPickerVerts);

        // Copy the original vertex positions into picker slots 0..nOrigVerts-1.
        // Their pickColor stays 0 — they are never the provoking
        // vertex of any picker triangle, so the value is unused.
        for (let v = 0; v < nOrigVerts; v++) {
            const s = v * itemSize;
            const o = v * 3;
            pickerPositions[o + 0] = posArr[s];
            pickerPositions[o + 1] = posArr[s + 1];
            pickerPositions[o + 2] = posArr[s + 2];
            sourceVertexIndex[v] = v;
        }

        // Per-triangle: duplicate the third (provoking) vertex into
        // a new picker-vertex slot and rewrite the triangle's index.
        for (let ti = 0; ti < nTris; ti++) {
            const a = indices[ti * 3];
            const b = indices[ti * 3 + 1];
            const c = indices[ti * 3 + 2];
            const newIdx = nOrigVerts + ti;

            const cs = c * itemSize;
            const no = newIdx * 3;
            pickerPositions[no + 0] = posArr[cs];
            pickerPositions[no + 1] = posArr[cs + 1];
            pickerPositions[no + 2] = posArr[cs + 2];

            pickerColors[no + 0] = triColor[ti * 3];
            pickerColors[no + 1] = triColor[ti * 3 + 1];
            pickerColors[no + 2] = triColor[ti * 3 + 2];

            pickerIndices[ti * 3 + 0] = a;
            pickerIndices[ti * 3 + 1] = b;
            pickerIndices[ti * 3 + 2] = newIdx;

            sourceVertexIndex[newIdx] = c;
        }

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(pickerPositions, 3));
        geometry.setAttribute("pickColor", new THREE.BufferAttribute(pickerColors, 3, true));
        geometry.setIndex(new THREE.BufferAttribute(pickerIndices, 1));

        const sourceMorphAttrs: (THREE.BufferAttribute | null)[] = [];
        let morphMemBytes = 0;
        if (sourceMorphs && sourceMorphs.length > 0) {
            geometry.morphAttributes.position = [];
            for (let m = 0; m < sourceMorphs.length; m++) {
                const src = sourceMorphs[m];
                const dup = this.duplicateMorphAttr(src, sourceVertexIndex);
                geometry.morphAttributes.position.push(new THREE.BufferAttribute(dup, 3));
                sourceMorphAttrs.push(src);
                morphMemBytes += dup.byteLength;
            }
            geometry.morphTargetsRelative = morphTargetsRelative;
        }

        return {
            geometry,
            sourceVertexIndex,
            sourceMorphAttrs,
            byteLength:
                pickerPositions.byteLength
                + pickerColors.byteLength
                + pickerIndices.byteLength
                + morphMemBytes,
        };
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

    /** Detect a source-morph attribute swap (applyField re-runs and
     *  reassigns ``mesh.geometry.morphAttributes.position`` to a new
     *  array of BufferAttributes on every step/scale change) and
     *  rebuild the picker's duplicated deltas in place. The picker
     *  morph BufferAttribute itself is reused so Three's GPU buffer
     *  ID stays valid; we just refresh its underlying typed array
     *  and bump ``needsUpdate``. Returns true if any rebuild happened. */
    private refreshMorphIfStale(
        mesh: CustomBatchedMesh,
        reg: RegisteredMesh,
    ): boolean {
        if (reg.morphTargetCount === 0) return false;
        const srcMorphs = (mesh.geometry as THREE.BufferGeometry)
            .morphAttributes?.position as THREE.BufferAttribute[] | undefined;
        if (!srcMorphs || srcMorphs.length === 0) return false;

        const pickerMorphs = (reg.pickerMesh.geometry as THREE.BufferGeometry)
            .morphAttributes.position as THREE.BufferAttribute[] | undefined;
        if (!pickerMorphs || pickerMorphs.length !== srcMorphs.length) {
            // Morph count changed (gained/lost a target). We don't
            // support this path yet — the shader was compiled for the
            // original count. Skip silently; next click falls through
            // to raycast for affected meshes.
            return false;
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
            if (reg.morphTargetCount === 0) return;
            this.refreshMorphIfStale(o, reg);
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
