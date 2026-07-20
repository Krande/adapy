// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials';
import {buildEdgeGeometryWithRangeIds, makeEdgeShaderMaterial} from './EdgeShaderHelper';
import {DesignDataExtension, SimulationDataExtensionMetadata} from "@/extensions/design_and_analysis_extension";


export class CustomBatchedMesh extends THREE.Mesh {
    originalGeometry: THREE.BufferGeometry;
    originalMaterial: THREE.Material;
    drawRanges: Map<string, [number, number]>;

    public readonly unique_key: string;
    public readonly is_design: boolean;
    public readonly ada_ext_data: SimulationDataExtensionMetadata | DesignDataExtension | null;

    private selectedRanges = new Set<string>();
    private hiddenRanges = new Set<string>();
    /** Bumped on every hide/unhide. Lets external consumers (e.g.
     *  GpuMeshPicker) cheaply detect that they need to re-sync the
     *  hidden set without exposing its identity. */
    public hiddenChangeCounter = 0;

    /** Lazy cache of drawRanges sorted by start offset, stored as
     *  parallel arrays so we walk by index (no per-entry object
     *  allocation, fewer GC pauses).
     *
     *  Both ``updateGroups`` and the GPU picker's hidden-range sync
     *  need this same sorted view, and FEA meshes can have 48k+
     *  drawRanges — re-sorting on every hide/select used to dominate
     *  the hide-click frame. ``drawRanges`` is only mutated at
     *  construction by the loader, so a one-shot build is safe; the
     *  cache is invalidated if anyone ever mutates it post-hoc by
     *  calling ``invalidateSortedSegments``. */
    private _sortedSegIds?: string[];
    private _sortedSegStarts?: Uint32Array;
    private _sortedSegCounts?: Uint32Array;

    private edgeMesh?: THREE.LineSegments;
    private rangeIdToIndex?: Map<string, number>;
    private edgeMaterial?: THREE.ShaderMaterial;
    public edgesEligible = false; // true once a design edge overlay was built (persists across rebuilds)

    // Cached materials to avoid per-click allocations for non-vertex-colored meshes
    private _matSelected?: THREE.Material;
    private _matInvisible?: THREE.Material;

    // Selection coloring helpers
    private _usesVertexColorsFlag: boolean = false;
    private _baseColors?: Float32Array; // snapshot of current animated/base colors
    private _selectionOverlay?: THREE.Mesh;
    // Independent overlay for per-face highlighting (opt-in face picking) — a single index sub-range
    // drawn in a distinct colour on top of object selection. Separate from _selectionOverlay so the
    // two never fight over materials/groups.
    private _faceOverlay?: THREE.Mesh;
    private _overlaySourceIndices?: Uint32Array; // mapping: overlay vertex i -> base vertex index

    // Class properties for raycasting
    private _raycast_inverseMatrix = new THREE.Matrix4();
    private _raycast_ray = new THREE.Ray();
    private _raycast_sphere = new THREE.Sphere();
    private _raycast_vA = new THREE.Vector3();
    private _raycast_vB = new THREE.Vector3();
    private _raycast_vC = new THREE.Vector3();
    private _raycast_uvA = new THREE.Vector2();
    private _raycast_uvB = new THREE.Vector2();
    private _raycast_uvC = new THREE.Vector2();
    private _raycast_intersectionPoint = new THREE.Vector3();
    private _raycast_barycoord = new THREE.Vector3();

    constructor(
        geometry: THREE.BufferGeometry,
        material: THREE.Material,
        drawRanges: Map<string, [number, number]>,
        unique_key: string,
        is_design: boolean,
        ada_ext_data: SimulationDataExtensionMetadata | DesignDataExtension | null
    ) {
        // Share the source geometry instead of clone()-ing it: the caller discards
        // the original mesh right after this constructor, so the clone only doubled
        // the model's buffers (~+0.8 GB on a big CAD GLB). Group/colour mutations on
        // this.geometry are safe — nothing else renders the source geometry.
        super(geometry, material.clone());
        this.originalGeometry = geometry;
        this.originalMaterial = material.clone();
        this.drawRanges = drawRanges;
        this.unique_key = unique_key;
        this.is_design = is_design;
        this.ada_ext_data = ada_ext_data;

        // Initialize cached materials to avoid per-click allocations
        this._matSelected = selectedMaterial.clone();
        this._matInvisible = new THREE.MeshBasicMaterial({ visible: false });

        // Determine if this mesh uses vertex colors initially
        this._recomputeUsesVertexColorsFlag();
        this.updateGroups();
    }

    /** Build (or rebuild) the sorted-segments cache. O(N log N) once.
     *  Stores parallel typed arrays + a string id array — Uint32 starts
     *  and counts are ~12 bytes/segment vs. ~64 bytes/segment for the
     *  object-wrapper layout, so for a 48k-range mesh this is ~600 KB
     *  rather than ~3 MB. */
    private buildSortedSegments(): void {
        const n = this.drawRanges.size;
        const order = new Array<number>(n);
        const ids = new Array<string>(n);
        const starts = new Uint32Array(n);
        const counts = new Uint32Array(n);
        let i = 0;
        for (const [id, [s, c]] of this.drawRanges) {
            ids[i] = id;
            starts[i] = s;
            counts[i] = c;
            order[i] = i;
            i++;
        }
        // Sort indices into ``order`` rather than zipped objects to
        // avoid 48k tiny object allocations on big FEA meshes.
        order.sort((a, b) => starts[a] - starts[b]);
        const sortedIds = new Array<string>(n);
        const sortedStarts = new Uint32Array(n);
        const sortedCounts = new Uint32Array(n);
        for (let j = 0; j < n; j++) {
            const k = order[j];
            sortedIds[j] = ids[k];
            sortedStarts[j] = starts[k];
            sortedCounts[j] = counts[k];
        }
        this._sortedSegIds = sortedIds;
        this._sortedSegStarts = sortedStarts;
        this._sortedSegCounts = sortedCounts;
    }

    /** Read-only view of drawRanges sorted by start offset. Builds
     *  the cache on first access. Returned arrays are owned by the
     *  mesh — do not mutate. */
    public getSortedSegments(): {
        ids: ReadonlyArray<string>;
        starts: Uint32Array;
        counts: Uint32Array;
    } {
        if (!this._sortedSegIds) this.buildSortedSegments();
        return {
            ids: this._sortedSegIds!,
            starts: this._sortedSegStarts!,
            counts: this._sortedSegCounts!,
        };
    }

    /** Drop the cache. Call after any external mutation to
     *  ``drawRanges`` — the loader currently never does this
     *  post-construction, but defensive in case that changes. */
    public invalidateSortedSegments(): void {
        this._sortedSegIds = undefined;
        this._sortedSegStarts = undefined;
        this._sortedSegCounts = undefined;
    }

    private updateGroups() {
        const idxCount = this.geometry.index!.count;
        this.geometry.clearGroups();
        // Recompute whether we use vertex colors based on current state
        this._recomputeUsesVertexColorsFlag();
        // If vertex coloring is not active, ensure any selection overlay is removed
        if (!this._usesVertexColorsFlag) {
            this._disposeSelectionOverlay();
        }
        // Keep three material slots: 0=original, 1=selected overlay (only for non-vertex-colored), 2=invisible
        // Use cached instances to avoid per-selection allocations
        this.material = [
            this.originalMaterial,
            this._matSelected!,
            this._matInvisible!
        ];

        // THREE renders one draw call PER geometry.group, regardless of
        // material count. The previous implementation added one group per
        // drawRange unconditionally — for a large ship FEM-style mesh with
        // ~48k selectable elements that's 48k draw calls and ~10 FPS,
        // even with nothing selected. Coalesce consecutive same-material
        // ranges into one group: in the common case (nothing selected,
        // no vertex-colour selection mode) the whole index buffer
        // collapses to a single draw call. Picking still works because
        // the ``drawRanges`` Map is the identity-of-element source of
        // truth (raycast hit → faceIndex → range lookup); THREE's
        // groups only carry material assignment.

        // Fast path: nothing diverges from the default material. One
        // group, one draw call. Vertex-colour selection is visualised
        // via the selection overlay, so it doesn't need per-range
        // groups either.
        const hasOverrides =
            this.hiddenRanges.size > 0 ||
            (!this._usesVertexColorsFlag && this.selectedRanges.size > 0);
        if (!hasOverrides) {
            this.geometry.addGroup(0, idxCount, 0);
            return;
        }

        const segs = this.getSortedSegments();
        const n = segs.ids.length;

        // Walk segments, merging default-material runs (including any
        // gaps between segments) into a single addGroup at flush time.
        // Only selected/hidden ranges produce their own groups.
        let cur = 0;
        let runStart: number | null = null;
        const flushRun = (end: number) => {
            if (runStart !== null && end > runStart) {
                this.geometry.addGroup(runStart, end - runStart, 0);
            }
            runStart = null;
        };

        for (let i = 0; i < n; i++) {
            const id = segs.ids[i];
            const s = segs.starts[i];
            const c = segs.counts[i];
            if (s > cur && runStart === null) {
                runStart = cur;
            }
            const mi: 0 | 1 | 2 = this.hiddenRanges.has(id)
                ? 2
                : this.selectedRanges.has(id)
                    ? (this._usesVertexColorsFlag ? 0 : 1)
                    : 0;
            if (mi === 0) {
                if (runStart === null) runStart = s;
            } else {
                flushRun(s);
                this.geometry.addGroup(s, c, mi);
            }
            cur = s + c;
        }
        // Trailing default region or open run extends to idxCount.
        if (cur < idxCount && runStart === null) runStart = cur;
        flushRun(idxCount);
    }

    /** call this when you have a renderer and want the overlay in the scene */
    public getEdgeOverlay(renderer: THREE.WebGLRenderer): THREE.LineSegments {
        this.edgesEligible = true; // this mesh takes a design edge overlay (FEA meshes never call this)
        if (!this.edgeMesh) {
            // first‐time initialization
            const {geometry, rangeIdToIndex} =
                buildEdgeGeometryWithRangeIds(this.originalGeometry, this.drawRanges);
            this.rangeIdToIndex = rangeIdToIndex;
            // The overlay is added as a SIBLING of this mesh (prepareLoadedModel adds both to the
            // same parent; refreshEdgeOverlays re-adds to `old.parent ?? mesh.parent`), so bake
            // only this mesh's LOCAL matrix — the scene graph contributes the ancestors when it
            // renders. Baking matrixWorld double-counted every ancestor transform. That hid at
            // load, where setupModelLoader centers the model by moving gltf_scene AFTER the meshes
            // are prepared: the ancestors were still at the origin, so world == local. A LIVE
            // rebuild (the Scene→Mesh panel's "Triangles" toggle -> refreshEdgeOverlays) bakes a
            // world matrix that already contains the centering, and the graph applies it again —
            // drawing the edges offset from their mesh by exactly the centering translation.
            this.updateMatrix();
            const localMat = this.matrix;

            this.edgeMaterial = makeEdgeShaderMaterial(renderer, rangeIdToIndex.size);
            this.edgeMesh = new THREE.LineSegments(geometry, this.edgeMaterial);
            this.edgeMesh.layers.set(1);
            // now *after* you’ve extracted the lines, bake the transform:
            this.edgeMesh.applyMatrix4(localMat);
        }
        return this.edgeMesh;
    }

    /** Drop the cached edge overlay so the next getEdgeOverlay() rebuilds it from the CURRENT options
     *  (e.g. after hideTessellationEdges toggled feature-only vs full-triangulation edges). Returns the
     *  old LineSegments so the caller can remove it from the scene before re-adding the rebuilt one. */
    public invalidateEdgeOverlay(): THREE.LineSegments | undefined {
        const old = this.edgeMesh;
        (this.edgeMesh?.geometry as THREE.BufferGeometry | undefined)?.dispose();
        this.edgeMaterial?.dispose();
        this.edgeMesh = undefined;
        this.edgeMaterial = undefined;
        this.rangeIdToIndex = undefined;
        return old;
    }

    public updateSelectionGroups(rangeIds: string[]) {
        this.selectedRanges = new Set(rangeIds);

        if (this._usesVertexColorsFlag) {
            // When using vertex colors, do not recolor the base mesh. Instead, build a face overlay
            // by duplicating the selected triangles into a child mesh using selectedMaterial.
            this._rebuildSelectionOverlay(rangeIds);
        }

        // Update groups regardless (to maintain hidden/selected visibility and non-vertex-colored behavior)
        this.updateGroups();

        // Edge overlay highlight. Selection is dispatched to EVERY
        // CustomBatchedMesh in the model, but each mesh's rangeIdToIndex only
        // covers the objects in its own buffer. Highlight the first selected id
        // this mesh actually owns; if it owns none, clear to -1. Using a bare
        // ``get(rangeIds[0])!`` here pushed ``undefined`` into the int uniform
        // (coerced to 0) on every non-owning mesh, so selecting any object lit
        // up the range-index-0 object's edges on all the other buffers.
        if (this.edgeMaterial && this.rangeIdToIndex) {
            let highlighted = -1;
            for (const id of rangeIds) {
                const idx = this.rangeIdToIndex.get(id);
                if (idx !== undefined) {
                    highlighted = idx;
                    break;
                }
            }
            this.edgeMaterial.uniforms.uHighlighted.value = highlighted;
        }
    }

    /** Capture the current geometry color attribute as the new base for future selection overlays */
    public setBaseColorsFromCurrent(): void {
        const attr = this.geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
        if (attr) {
            const arr = attr.array as Float32Array;
            this._baseColors = new Float32Array(arr.length);
            this._baseColors.set(arr);
            // Ensure vertex colors flag is on when base colors exist
            this._recomputeUsesVertexColorsFlag();
            // also ensure original material enables vertex colors
            const mat0 = (Array.isArray(this.material) ? (this.material as THREE.Material[])[0] : this.originalMaterial) as any;
            if (mat0 && 'vertexColors' in mat0) {
                mat0.vertexColors = true;
                (mat0 as THREE.Material).needsUpdate = true;
            }
        }
    }

    /** Reapply current selection highlighting on top of base colors */
    public reapplySelectionHighlight(): void {
        this.updateSelectionGroups(Array.from(this.selectedRanges));
    }

    /** Colour specific draw-ranges (by rangeId) via per-vertex colours.
     *
     *  Used by viewer utilities (e.g. diff) to recolour elements in place. Ranges
     *  not present in ``colorByRangeId`` keep ``baseColor`` (a neutral grey by
     *  default) so the diff result reads cleanly. Coexists with selection
     *  highlighting (vertex-colour path); call
     *  :meth:`disableVertexColorsAndResetMaterial` to reset to the original look.
     */
    public setRangeColors(
        colorByRangeId: Map<string, THREE.Color>,
        baseColor: THREE.Color = new THREE.Color(0.62, 0.62, 0.62),
    ): void {
        const index = this.geometry.index;
        const pos = this.geometry.getAttribute('position');
        if (!index || !pos) return;
        let attr = this.geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
        if (!attr) {
            const arr = new Float32Array(pos.count * 3);
            for (let i = 0; i < pos.count; i++) {
                arr[i * 3] = baseColor.r;
                arr[i * 3 + 1] = baseColor.g;
                arr[i * 3 + 2] = baseColor.b;
            }
            attr = new THREE.BufferAttribute(arr, 3);
            this.geometry.setAttribute('color', attr);
        }
        for (const [rangeId, col] of colorByRangeId) {
            const rng = this.drawRanges.get(rangeId);
            if (!rng) continue;
            const [start, count] = rng;
            for (let j = start; j < start + count; j++) {
                const vid = index.getX(j);
                attr.setXYZ(vid, col.r, col.g, col.b);
            }
        }
        attr.needsUpdate = true;
        this.setBaseColorsFromCurrent();
        this.updateGroups();
        this.reapplySelectionHighlight();
    }

    /** Disable vertex colors and restore original material behavior for non-vertex-colored mode */
    public disableVertexColorsAndResetMaterial(): void {
        // Remove color attribute from working geometry
        if (this.geometry.getAttribute('color')) {
            this.geometry.deleteAttribute('color');
        }
        this._baseColors = undefined;
        // Turn off vertexColors on material slot 0 (original material)
        const mat0 = (Array.isArray(this.material) ? (this.material as THREE.Material[])[0] : this.originalMaterial) as any;
        if (mat0 && 'vertexColors' in mat0) {
            mat0.vertexColors = false;
            (mat0 as THREE.Material).needsUpdate = true;
        }
        this._recomputeUsesVertexColorsFlag();
        this._disposeSelectionOverlay();
        // Rebuild groups so selection uses material index swap again
        this.updateGroups();
    }

    /** Internal: recompute whether vertex colors are active based on current geometry/material */
    private _recomputeUsesVertexColorsFlag(): void {
        const hasColorAttr = !!this.geometry.getAttribute('color');
        let usesVC = false;
        // check material 0 if array, else originalMaterial
        const mat0 = (Array.isArray(this.material) ? (this.material as THREE.Material[])[0] : this.originalMaterial) as any;
        if (hasColorAttr && mat0 && 'vertexColors' in mat0) {
            usesVC = !!mat0.vertexColors;
        }
        // also consider if we have a base color snapshot
        this._usesVertexColorsFlag = usesVC || !!this._baseColors;
    }

    /** Free every GPU resource this mesh owns (geometry + all cloned materials + the edge
     * picker mesh/material + any selection overlay). three.js only releases VRAM on an
     * explicit dispose() — detaching from the scene graph (Object3D.clear()/remove()) does
     * NOT — so this must run when a model is cleared/replaced or the geometry/texture memory
     * never falls. Idempotent; only disposes per-instance clones, never shared singletons. */
    dispose(): void {
        this._disposeSelectionOverlay();
        this.clearFaceHighlight();
        if (this.edgeMesh) {
            (this.edgeMesh.geometry as THREE.BufferGeometry | undefined)?.dispose();
            this.edgeMesh = undefined;
        }
        this.edgeMaterial?.dispose();
        this.edgeMaterial = undefined;
        const mats = Array.isArray(this.material) ? this.material : [this.material];
        for (const m of mats) (m as THREE.Material | undefined)?.dispose();
        this.originalMaterial?.dispose();
        this._matSelected?.dispose();
        this._matInvisible?.dispose();
        this._matSelected = undefined;
        this._matInvisible = undefined;
        // this.geometry IS originalGeometry (shared at construction, not cloned).
        this.geometry?.dispose();
    }

    private _disposeSelectionOverlay(): void {
        if (this._selectionOverlay) {
            // Remove from scene graph
            this.remove(this._selectionOverlay);
            // IMPORTANT: Do not dispose geometry, as it references base geometry attributes/morphs.
            // Only dispose the material(s) we cloned for the overlay to free GPU resources.
            const m = this._selectionOverlay.material as THREE.Material | THREE.Material[];
            if (Array.isArray(m)) {
                for (const mm of m) mm.dispose();
            } else if (m) {
                m.dispose();
            }
            this._selectionOverlay = undefined;
        }
    }

    /** Highlight a single index sub-range (one source face) in a distinct colour, on top of object
     *  selection. start/length are absolute positions into this mesh's index buffer. Independent of
     *  the object-selection overlay. Pass length<=0 (or call clearFaceHighlight) to remove it. */
    public highlightFaceRange(start: number, length: number): void {
        const srcGeom = this.geometry as THREE.BufferGeometry;
        const idxAttr = srcGeom.getIndex();
        const idxCount = idxAttr ? idxAttr.count : (srcGeom.attributes.position?.count ?? 0);
        if (!(length > 0) || idxCount === 0 || start < 0 || start >= idxCount) {
            this.clearFaceHighlight();
            return;
        }
        const s = start;
        const c = Math.min(length, idxCount - start);

        let overlayGeom: THREE.BufferGeometry;
        if (this._faceOverlay) {
            overlayGeom = this._faceOverlay.geometry as THREE.BufferGeometry;
        } else {
            // Reference (do NOT clone) the base attributes/index + morphs so the GPU morphs it for free
            // and no extra vertex memory is used — same approach as the selection overlay.
            overlayGeom = new THREE.BufferGeometry();
            if (srcGeom.index) overlayGeom.setIndex(srcGeom.index);
            for (const name of Object.keys(srcGeom.attributes))
                overlayGeom.setAttribute(name, srcGeom.getAttribute(name));
            overlayGeom.morphAttributes = {} as any;
            if (srcGeom.morphAttributes)
                for (const mName of Object.keys(srcGeom.morphAttributes))
                    (overlayGeom.morphAttributes as any)[mName] = (srcGeom.morphAttributes as any)[mName];
            overlayGeom.morphTargetsRelative = srcGeom.morphTargetsRelative === true;

            // Same blue as object selection — in Faces mode only the clicked face is painted (the rest
            // of the solid keeps its normal colour), so it reads as a face-granular selection.
            const visMat = selectedMaterial.clone();
            visMat.side = THREE.DoubleSide;
            (visMat as any).morphTargets = true;
            (visMat as any).polygonOffset = true;
            (visMat as any).polygonOffsetFactor = -2; // sit in front of the base surface
            (visMat as any).polygonOffsetUnits = -2;
            const invMat = new THREE.MeshBasicMaterial({visible: false});
            (invMat as any).morphTargets = true;

            this._faceOverlay = new THREE.Mesh(overlayGeom, [visMat, invMat]);
            this._faceOverlay.matrixAutoUpdate = true;
            this._faceOverlay.layers.mask = this.layers.mask;
            this.add(this._faceOverlay);
        }
        (this._faceOverlay as any).morphTargetInfluences = (this as any).morphTargetInfluences;
        (this._faceOverlay as any).morphTargetDictionary = (this as any).morphTargetDictionary;

        // groups: [0,s) invisible, [s,s+c) the visible face, [s+c,end) invisible
        overlayGeom.clearGroups();
        if (s > 0) overlayGeom.addGroup(0, s, 1);
        overlayGeom.addGroup(s, c, 0);
        if (s + c < idxCount) overlayGeom.addGroup(s + c, idxCount - (s + c), 1);
    }

    /** Remove the per-face highlight overlay. Disposes only the cloned materials (the geometry shares
     *  base attributes — disposing it would free the base mesh's GPU buffers). */
    public clearFaceHighlight(): void {
        if (!this._faceOverlay) return;
        this.remove(this._faceOverlay);
        const m = this._faceOverlay.material as THREE.Material | THREE.Material[];
        (Array.isArray(m) ? m : [m]).forEach((x) => x && x.dispose());
        this._faceOverlay = undefined;
    }

    private _rebuildSelectionOverlay(rangeIds: string[]): void {
        // Clear overlay when nothing selected
        if (!rangeIds || rangeIds.length === 0) {
            this._disposeSelectionOverlay();
            this._overlaySourceIndices = undefined; // ensures CPU updater is skipped
            return;
        }

        const srcGeom = this.geometry as THREE.BufferGeometry;
        const posAttr = srcGeom.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (!posAttr) {
            this._disposeSelectionOverlay();
            this._overlaySourceIndices = undefined;
            return;
        }

        // Build or update overlay geometry that shares base attributes and morphs.
        // We only manipulate geometry groups to draw selected ranges; GPU handles morphing.
        let overlayGeom: THREE.BufferGeometry;
        let overlayMat: THREE.Material;

        if (this._selectionOverlay) {
            overlayGeom = (this._selectionOverlay as THREE.Mesh).geometry as THREE.BufferGeometry;
            overlayMat = (this._selectionOverlay as THREE.Mesh).material as THREE.Material;
            // Ensure overlay is referencing base attributes; if not, rebuild references
            if (!overlayGeom.getAttribute('position') || overlayGeom.getAttribute('position') !== srcGeom.getAttribute('position')) {
                overlayGeom = new THREE.BufferGeometry();
                // Reference base attributes/index (do NOT clone to avoid extra memory and to leverage GPU morphs)
                srcGeom.index && overlayGeom.setIndex(srcGeom.index);
                for (const name of Object.keys(srcGeom.attributes)) {
                    overlayGeom.setAttribute(name, srcGeom.getAttribute(name));
                }
                // Morph attributes
                overlayGeom.morphAttributes = {} as any;
                if (srcGeom.morphAttributes) {
                    for (const mName of Object.keys(srcGeom.morphAttributes)) {
                        // @ts-ignore
                        overlayGeom.morphAttributes[mName] = (srcGeom.morphAttributes as any)[mName];
                    }
                }
                overlayGeom.morphTargetsRelative = srcGeom.morphTargetsRelative === true;
                (this._selectionOverlay as THREE.Mesh).geometry = overlayGeom;
            }
            // Ensure material is configured for GPU morphing and polygon offset
            (overlayMat as any).morphTargets = true;
            overlayMat.side = THREE.DoubleSide;
            (overlayMat as any).polygonOffset = true;
            (overlayMat as any).polygonOffsetFactor = -1;
            (overlayMat as any).polygonOffsetUnits = -1;
        } else {
            overlayGeom = new THREE.BufferGeometry();
            srcGeom.index && overlayGeom.setIndex(srcGeom.index);
            for (const name of Object.keys(srcGeom.attributes)) {
                overlayGeom.setAttribute(name, srcGeom.getAttribute(name));
            }
            overlayGeom.morphAttributes = {} as any;
            if (srcGeom.morphAttributes) {
                for (const mName of Object.keys(srcGeom.morphAttributes)) {
                    // @ts-ignore
                    overlayGeom.morphAttributes[mName] = (srcGeom.morphAttributes as any)[mName];
                }
            }
            overlayGeom.morphTargetsRelative = srcGeom.morphTargetsRelative === true;

            overlayMat = selectedMaterial.clone();
            (overlayMat as any).morphTargets = true; // let GPU handle morphs
            overlayMat.side = THREE.DoubleSide;
            (overlayMat as any).polygonOffset = true;
            (overlayMat as any).polygonOffsetFactor = -1;
            (overlayMat as any).polygonOffsetUnits = -1;

            this._selectionOverlay = new THREE.Mesh(overlayGeom, overlayMat);
            this._selectionOverlay.matrixAutoUpdate = true;
            this._selectionOverlay.layers.mask = this.layers.mask;
            this.add(this._selectionOverlay);
        }

        // Build overlay materials as an array so Three.js respects geometry groups
        const visibleSelMat = overlayMat instanceof Array ? (overlayMat[0] as THREE.Material) : overlayMat;
        const invisibleMat = new THREE.MeshBasicMaterial({ visible: false });
        // Ensure morphTargets on both
        (visibleSelMat as any).morphTargets = true;
        (invisibleMat as any).morphTargets = true;
        // Reduce z-fighting for the visible overlay
        (visibleSelMat as any).polygonOffset = true;
        (visibleSelMat as any).polygonOffsetFactor = -1;
        (visibleSelMat as any).polygonOffsetUnits = -1;

        // Assign material array to overlay
        (this._selectionOverlay as THREE.Mesh).material = [visibleSelMat, invisibleMat];

        // Make overlay follow the same morph targets as the base mesh
        (this._selectionOverlay as any).morphTargetInfluences = (this as any).morphTargetInfluences;
        (this._selectionOverlay as any).morphTargetDictionary = (this as any).morphTargetDictionary;

        // Rebuild groups to cover the entire index range, using materialIndex 0 for selected, 1 for others/hidden
        overlayGeom.clearGroups();
        const idxAttr = srcGeom.getIndex();
        const idxCount = idxAttr ? idxAttr.count : (srcGeom.attributes.position?.count ?? 0);
        const segs = Array.from(this.drawRanges.entries())
            .map(([id, [s, c]]) => ({ id, s, c }))
            .sort((a, b) => a.s - b.s);

        let cur = 0;
        const selectedSet = new Set(rangeIds);
        for (const { id, s, c } of segs) {
            if (s > cur) overlayGeom.addGroup(cur, s - cur, 1); // gap = invisible
            let mi: 0 | 1 = 1; // default invisible
            if (!this.hiddenRanges.has(id) && selectedSet.has(id)) mi = 0; // show selected
            overlayGeom.addGroup(s, c, mi);
            cur = s + c;
        }
        if (cur < idxCount) overlayGeom.addGroup(cur, idxCount - cur, 1);

        // Ensure CPU updater is not used for this path
        this._overlaySourceIndices = undefined;
    }

    // Update overlay geometry positions each frame to match current morph-deformed shape
    private _updateSelectionOverlayFromMorphs(): void {
        if (!this._selectionOverlay || !this._overlaySourceIndices) return;
        const baseGeom = this.geometry as THREE.BufferGeometry;
        const posAttr = baseGeom.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (!posAttr) return;
        const morphPositions = (baseGeom.morphAttributes && baseGeom.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
        const morphTargetsRelative = baseGeom.morphTargetsRelative === true;
        const influences: number[] | undefined = (this as any).morphTargetInfluences;
        const overlayGeom = this._selectionOverlay.geometry as THREE.BufferGeometry;
        const oPosAttr = overlayGeom.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (!oPosAttr) return;

        const arr = oPosAttr.array as Float32Array;
        const srcIdx = this._overlaySourceIndices;
        const tmp = new THREE.Vector3();
        for (let i = 0, ov = 0; i < srcIdx.length; i++, ov += 3) {
            const vi = srcIdx[i];
            tmp.set(posAttr.getX(vi), posAttr.getY(vi), posAttr.getZ(vi));
            if (morphPositions && influences) {
                let sumInf = 0;
                for (let m = 0; m < morphPositions.length; m++) {
                    const inf = influences[m] || 0;
                    if (inf === 0) continue;
                    sumInf += inf;
                    const mp = morphPositions[m];
                    const mx = mp.getX(vi);
                    const my = mp.getY(vi);
                    const mz = mp.getZ(vi);
                    if (morphTargetsRelative) {
                        tmp.x += mx * inf;
                        tmp.y += my * inf;
                        tmp.z += mz * inf;
                    } else {
                        tmp.x = tmp.x * (1 - sumInf) + mx * inf;
                        tmp.y = tmp.y * (1 - sumInf) + my * inf;
                        tmp.z = tmp.z * (1 - sumInf) + mz * inf;
                    }
                }
            }
            arr[ov] = tmp.x; arr[ov + 1] = tmp.y; arr[ov + 2] = tmp.z;
        }
        oPosAttr.needsUpdate = true;
        // Optionally, skip recomputing normals every frame for performance.
    }

    // Hook into render loop to keep overlay deformed with base morphs
    public onBeforeRender(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera, geometry: THREE.BufferGeometry, material: THREE.Material, group: any): void {
        this._updateSelectionOverlayFromMorphs();
    }

    public clearSelectionGroups() {
        this.updateSelectionGroups([]);
    }

    /**
     * Hide a batch of draw-ranges in one go.
     *
     * @param rangeIds A set (or other iterable) of draw-range IDs to hide.
     */
    public hideBatchDrawRange(rangeIds: Iterable<string>): void {
        // 1) Mark them hidden
        for (const id of rangeIds) {
            this.hiddenRanges.add(id);
        }
        this.hiddenChangeCounter++;

        // 2) Rebuild all groups just once
        this.updateGroups();

        // 3) Update the edge-overlay texture in one shot
        if (this.edgeMaterial && this.rangeIdToIndex) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            const data = tex.image.data as Uint8Array;
            for (const id of rangeIds) {
                const idx = this.rangeIdToIndex.get(id);
                if (idx !== undefined) {
                    data[idx] = 0;
                }
            }
            tex.needsUpdate = true;
        }
    }

    public getHiddenRanges(): ReadonlySet<string> {
        return this.hiddenRanges;
    }

    public unhideAllDrawRanges() {
        this.hiddenRanges.clear();
        this.hiddenChangeCounter++;
        this.updateGroups();
        if (this.edgeMaterial) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            (tex.image.data as Uint8Array).fill(255);
            this.edgeMaterial.uniforms.uHighlighted.value = -1;
            tex.needsUpdate = true;
        }
    }

    /**
     * Overrides the raycast method to ignore hidden draw ranges.
     */
    raycast(raycaster: THREE.Raycaster, intersects: THREE.Intersection[]): void {
        const material = this.material;

        if (material === undefined) return;

        // Compute the bounding sphere if necessary
        if (this.geometry.boundingSphere === null) this.geometry.computeBoundingSphere();

        // Check bounding sphere distance to ray
        const sphere = this._raycast_sphere.copy(this.geometry.boundingSphere!);
        sphere.applyMatrix4(this.matrixWorld);

        if (raycaster.ray.intersectsSphere(sphere) === false) return;

        // Transform the ray into the local space of the mesh
        const inverseMatrix = this._raycast_inverseMatrix.copy(this.matrixWorld).invert();

        const localRay = this._raycast_ray.copy(raycaster.ray).applyMatrix4(inverseMatrix);

        // Determine if we have an array of materials
        const isMultiMaterial = Array.isArray(material);

        const materials = isMultiMaterial ? (material as THREE.Material[]) : [material as THREE.Material];

        // Loop over the geometry's groups
        const groups = this.geometry.groups;

        for (let i = 0; i < groups.length; i++) {
            const group = groups[i];
            const groupMaterial = group.materialIndex !== undefined ? materials[group.materialIndex] : undefined;

            if (groupMaterial === undefined) continue;

            if (groupMaterial.visible === false) continue;

            // Perform raycasting on this group
            this.raycastGroup(localRay, raycaster, group, groupMaterial, intersects);
        }
    }

    /**
     * Performs raycasting on a specific group of the geometry.
     */
    private raycastGroup(
        localRay: THREE.Ray,
        raycaster: THREE.Raycaster,
        group: { start: number; count: number; materialIndex?: number },
        material: THREE.Material,
        intersects: THREE.Intersection[]
    ): void {
        const geometry = this.geometry as THREE.BufferGeometry;
        const index = geometry.index;
        const position = geometry.attributes.position;
        const morphPositions = (geometry.morphAttributes && geometry.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
        const morphTargetsRelative = geometry.morphTargetsRelative === true;
        const morphInfluences: number[] | undefined = (this as any).morphTargetInfluences;

        if (position === undefined) return;

        const start = group.start;
        const end = start + group.count;

        const vA = this._raycast_vA;
        const vB = this._raycast_vB;
        const vC = this._raycast_vC;

        // helper to apply morph target deformation per vertex index into target vector
        const applyMorph = (idx: number, target: THREE.Vector3) => {
            if (!morphPositions || !morphInfluences) return;
            let sumInfluence = 0;
            for (let i = 0; i < morphPositions.length; i++) {
                const inf = morphInfluences[i] || 0;
                if (inf === 0) continue;
                sumInfluence += inf;
                const mp = morphPositions[i];
                const mx = mp.getX(idx);
                const my = mp.getY(idx);
                const mz = mp.getZ(idx);
                if (morphTargetsRelative) {
                    target.x += mx * inf;
                    target.y += my * inf;
                    target.z += mz * inf;
                } else {
                    // absolute morph targets: blend base towards target
                    target.x = target.x * (1 - sumInfluence) + mx * inf;
                    target.y = target.y * (1 - sumInfluence) + my * inf;
                    target.z = target.z * (1 - sumInfluence) + mz * inf;
                }
            }
        };

        let intersection;

        if (index !== null) {
            // Indexed geometry
            const indices = index.array as Uint16Array | Uint32Array;

            for (let i = start; i < end; i += 3) {
                const a = indices[i];
                const b = indices[i + 1];
                const c = indices[i + 2];

                // fetch base positions
                vA.fromBufferAttribute(position, a);
                vB.fromBufferAttribute(position, b);
                vC.fromBufferAttribute(position, c);
                // apply morph offsets if any
                applyMorph(a, vA);
                applyMorph(b, vB);
                applyMorph(c, vC);

                intersection = this.checkBufferGeometryIntersection(
                    this,
                    material,
                    raycaster,
                    localRay,
                    vA,
                    vB,
                    vC,
                    a,
                    b,
                    c,
                    group.materialIndex
                );

                if (intersection) {
                    intersection.faceIndex = Math.floor(i / 3);
                    intersects.push(intersection);
                }
            }
        } else {
            // Non-indexed geometry
            for (let i = start; i < end; i += 3) {
                const a = i;
                const b = i + 1;
                const c = i + 2;

                vA.fromBufferAttribute(position, a);
                vB.fromBufferAttribute(position, b);
                vC.fromBufferAttribute(position, c);
                applyMorph(a, vA);
                applyMorph(b, vB);
                applyMorph(c, vC);

                intersection = this.checkBufferGeometryIntersection(
                    this,
                    material,
                    raycaster,
                    localRay,
                    vA,
                    vB,
                    vC,
                    a,
                    b,
                    c,
                    group.materialIndex
                );

                if (intersection) {
                    intersection.faceIndex = Math.floor(i / 3);
                    intersects.push(intersection);
                }
            }
        }
    }

    /**
     * Checks for an intersection between a ray and a triangle defined by vertex indices.
     */
    private checkBufferGeometryIntersection(
        object: THREE.Object3D,
        material: THREE.Material,
        raycaster: THREE.Raycaster,
        ray: THREE.Ray,
        vA: THREE.Vector3,
        vB: THREE.Vector3,
        vC: THREE.Vector3,
        a: number,
        b: number,
        c: number,
        materialIndex: number | undefined
    ): THREE.Intersection | null {
        const _vA = vA; // use provided morphed vertices
        const _vB = vB;
        const _vC = vC;
        const intersectionPoint = this._raycast_intersectionPoint;

        let side = material.side;

        if (side === undefined) side = THREE.FrontSide;

        const backfaceCulling = side === THREE.FrontSide;

        const intersect = ray.intersectTriangle(
            _vC,
            _vB,
            _vA,
            backfaceCulling,
            intersectionPoint
        );

        if (intersect === null) return null;

        intersectionPoint.applyMatrix4(this.matrixWorld);

        const distance = raycaster.ray.origin.distanceTo(intersectionPoint);

        if (distance < raycaster.near || distance > raycaster.far) return null;

        const uvAttribute = this.geometry.attributes.uv;
        let uv: THREE.Vector2 | undefined;

        if (uvAttribute) {
            // ``geometry.attributes.uv`` is typed as
            // ``BufferAttribute | InterleavedBufferAttribute``; both have
            // identical fromBufferAttribute behaviour at runtime, but
            // Vector2.fromBufferAttribute's signature only declares
            // ``BufferAttribute``. Cast through to satisfy the typing.
            const uvAttr = uvAttribute as THREE.BufferAttribute;
            const uvA = this._raycast_uvA.fromBufferAttribute(uvAttr, a);
            const uvB = this._raycast_uvB.fromBufferAttribute(uvAttr, b);
            const uvC = this._raycast_uvC.fromBufferAttribute(uvAttr, c);

            // Compute the UV coordinates at the intersection point
            uv = this._uvIntersection(_vA, _vB, _vC, uvA, uvB, uvC, intersectionPoint);
        }

        return {
            distance: distance,
            point: intersectionPoint.clone(),
            object: object,
            uv: uv,
            face: null, // Face3 is deprecated; set to null or provide custom data
            faceIndex: -1, // Can set to appropriate face index if needed
        } as THREE.Intersection;
    }

    /**
     * Computes the UV coordinates at the intersection point.
     */
    private _uvIntersection(
        vA: THREE.Vector3,
        vB: THREE.Vector3,
        vC: THREE.Vector3,
        uvA: THREE.Vector2,
        uvB: THREE.Vector2,
        uvC: THREE.Vector2,
        intersectionPoint: THREE.Vector3
    ): THREE.Vector2 {
        const barycoord = THREE.Triangle.getBarycoord(
            intersectionPoint,
            vA,
            vB,
            vC,
            this._raycast_barycoord
        );
        const uv = new THREE.Vector2();
        // getBarycoord returns null when the point is degenerate (zero-
        // area triangle); fall back to an empty UV in that case rather
        // than throwing.
        if (barycoord === null) return uv;
        uvA.multiplyScalar(barycoord.x);
        uvB.multiplyScalar(barycoord.y);
        uvC.multiplyScalar(barycoord.z);
        uv.add(uvA).add(uvB).add(uvC);
        return uv;
    }
}
