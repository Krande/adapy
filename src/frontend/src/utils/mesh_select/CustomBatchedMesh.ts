// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials';
import {buildEdgeGeometryAsync, buildEdgeGeometryWithRangeIds, makeEdgeShaderMaterial} from './EdgeShaderHelper';
import {DesignDataExtension, SimulationDataExtensionMetadata} from "@/extensions/design_and_analysis_extension";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {coalescedGroups} from "./groupRuns";
import {useVisibilityStore} from "@/state/visibilityStore";
import {followMorph, refreshMorphTexture} from "@/utils/scene/fea/gpuRefresh";
import {applyMorphTargets} from "./customBatchedMesh/morph";
import {prepareOverlayMaterial, sharedOverlayGeometry} from "./customBatchedMesh/selectionOverlay";
import {raycastBatchedMesh} from "./customBatchedMesh/raycast";
import {fillVertexColors, paintIndexedRange, rangeRgb} from "./customBatchedMesh/vertexPaint";


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

    private hiddenNotified = 0;
    /** The marks last set (`setMarks`), hidden ranges included. */
    private _marks: ReadonlyMap<string, number> | null = null;
    private readonly hiddenListeners = new Set<() => void>();

    /** Call `listener` after every hide, unhide and isolate of this mesh's ranges: for what draws its
     *  elements in a geometry of its own (the coloured beam lines, the result-point markers), which
     *  leaves the hidden ones out. Returns the unsubscribe. */
    public onHiddenChange(listener: () => void): () => void {
        this.hiddenListeners.add(listener);
        return () => void this.hiddenListeners.delete(listener);
    }

    private markHiddenChanged(): void {
        this.hiddenChangeCounter++;
        useVisibilityStore.getState().bump();
    }

    /** Bumped whenever the hidden or selected ranges change (every `updateGroups`). The adacpp
     *  WebGPU renderer re-reads the per-range state through `isRangeHidden` / `isRangeSelected`
     *  only when this moves, instead of interpreting three.js material groups. */
    public renderStateVersion = 0;

    public isRangeHidden(id: string): boolean {
        return this.hiddenRanges.has(id);
    }

    public isRangeSelected(id: string): boolean {
        return this.selectedRanges.has(id);
    }

    /** True for this mesh's own selection / face-highlight overlay children: three.js draws the
     *  highlight with them. The adacpp renderer colours selected ranges directly and skips the selection
     *  overlay; the face overlay it draws from `faceHighlightRange`. */
    public isHighlightOverlay(obj: THREE.Object3D): boolean {
        return obj === this._selectionOverlay || obj === this._faceOverlay || obj === this._markOverlay;
    }

    /** The highlighted face as a range of this mesh's index buffer, and the overlay that draws it in
     *  three.js; null when no face is highlighted. */
    public faceHighlightRange(): {overlay: THREE.Mesh; start: number; count: number} | null {
        const overlay = this._faceOverlay;
        const face = overlay?.geometry.groups.find((g) => g.materialIndex === 0);
        return overlay && face && face.count > 0 ? {overlay, start: face.start, count: face.count} : null;
    }

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
    /** Bumped whenever the overlay is dropped, so an async build that started before it is discarded. */
    private edgeGeneration = 0;
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
    // Objects a tool marks for a while (`setMarks`), each in its own colour, over the selection.
    private _markOverlay?: THREE.Mesh;
    private _overlaySourceIndices?: Uint32Array; // mapping: overlay vertex i -> base vertex index

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
        this._segIndexById = undefined;
        this._sortedSegIds = undefined;
        this._sortedSegStarts = undefined;
        this._sortedSegCounts = undefined;
    }

    private updateGroups() {
        this.renderStateVersion++;
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
        // Whatever draws these elements over this mesh hides with them (`syncAlignedOverlays`).
        for (const child of this.children) this.syncAlignedOverlay(child);
        // And what draws them in a shape of its own (beam lines, result points) is told.
        if (this.hiddenNotified !== this.hiddenChangeCounter) {
            this.hiddenNotified = this.hiddenChangeCounter;
            // A mark on an element hidden since it was set goes; one shown again comes back.
            if (this._marks?.size) this.setMarks(this._marks);
            for (const listener of [...this.hiddenListeners]) listener();
        }
        if (!hasOverrides) {
            this.geometry.addGroup(0, idxCount, 0);
            return;
        }

        // One group per run of neighbouring ranges that share a material -- selected and hidden runs
        // included, not only the default one (`groupRuns.coalescedGroups`). Gaps between ranges draw
        // with the default material.
        const segs = this.getSortedSegments();
        const groups = coalescedGroups(
            segs.starts,
            segs.counts,
            (i) => {
                const id = segs.ids[i];
                if (this.hiddenRanges.has(id)) return 2;
                if (this.selectedRanges.has(id)) return this._usesVertexColorsFlag ? 0 : 1;
                return 0;
            },
            idxCount,
            0,
        );
        for (const g of groups) this.geometry.addGroup(g.start, g.count, g.materialIndex);
    }

    /** Hidden in an overlay drawn over this mesh: never drawn, whatever the hidden appearance is. */
    private _matOverlayHidden = new THREE.MeshBasicMaterial({visible: false});

    /**
     * Whether `obj` draws this mesh's elements in its own geometry, corner for corner of this mesh's
     * index list: a copy of the mesh "de-indexed" (vertex k is index entry k, so a draw range is the
     * same numbers in it), with one material of its own. What an overlay painted over the elements
     * looks like -- core's element paint, a plugin's colour field hung on the mesh it got from
     * `getActiveFeaMesh`. Never anything indexed (a copy that shares this mesh's index or geometry --
     * a highlight, a posed copy -- draws by groups of its own, which this must not clear), nor one
     * that draws with several materials, nor another mesh with element ranges of its own.
     */
    public isAlignedOverlay(obj: THREE.Object3D): obj is THREE.Mesh {
        if (!(obj instanceof THREE.Mesh) || obj instanceof CustomBatchedMesh || this.isHighlightOverlay(obj)) return false;
        const geom = obj.geometry as THREE.BufferGeometry | undefined;
        const index = this.geometry.index;
        if (!geom || !index || geom === this.geometry || geom.index) return false;
        if (Array.isArray(obj.material) && obj.material[1] !== this._matOverlayHidden) return false;
        return geom.getAttribute("position")?.count === index.count;
    }

    /**
     * Give an aligned overlay (`isAlignedOverlay`) this mesh's hidden ranges: a hidden element has
     * no colour, no glyph face drawn over it either. Its own material draws the rest. Run on every
     * hide / unhide / isolate and when the overlay is added, so an overlay made (or remade, a View
     * setting changed) after a hide starts out with the hidden elements left out.
     */
    private syncAlignedOverlay(obj: THREE.Object3D): void {
        if (!this.isAlignedOverlay(obj)) return;
        const geom = obj.geometry as THREE.BufferGeometry;
        // Its own material: the one it draws with, or the first of the pair this gave it before.
        const current = obj.material;
        const own = Array.isArray(current) ? current[0] : current;
        geom.clearGroups();
        if (this.hiddenRanges.size === 0) {
            obj.material = own;
            return;
        }
        const segs = this.getSortedSegments();
        const total = this.geometry.index!.count;
        for (const g of coalescedGroups(segs.starts, segs.counts, (i) => (this.hiddenRanges.has(segs.ids[i]) ? 1 : 0), total, 0)) {
            geom.addGroup(g.start, g.count, g.materialIndex);
        }
        obj.material = [own, this._matOverlayHidden];
    }

    /** Children added are given the hidden ranges at once (`syncAlignedOverlay`). */
    override add(...objects: THREE.Object3D[]): this {
        super.add(...objects);
        for (const o of objects) this.syncAlignedOverlay(o);
        return this;
    }

    /** call this when you have a renderer and want the overlay in the scene */
    public getEdgeOverlay(renderer: THREE.WebGLRenderer): THREE.LineSegments {
        this.edgesEligible = true; // this mesh takes a design edge overlay (FEA meshes never call this)
        if (!this.edgeMesh) {
            // first‐time initialization
            this.assembleEdgeOverlay(renderer, buildEdgeGeometryWithRangeIds(this.originalGeometry, this.drawRanges));
        }
        return this.edgeMesh!;
    }

    /** `getEdgeOverlay`, with the geometry built in a worker -- what a model LOAD uses, so the mesh
     *  can be on screen while its edges are still being worked out. Null when the overlay was
     *  dropped (`invalidateEdgeOverlay`, `dispose`) while it was being built: that answer is for
     *  options or a mesh that no longer stand. */
    public async getEdgeOverlayAsync(renderer: THREE.WebGLRenderer): Promise<THREE.LineSegments | null> {
        this.edgesEligible = true;
        if (this.edgeMesh) return this.edgeMesh;
        const generation = ++this.edgeGeneration;
        const built = await buildEdgeGeometryAsync(this.originalGeometry, this.drawRanges);
        if (generation !== this.edgeGeneration) {
            built.geometry.dispose();
            return null;
        }
        // A synchronous build may have landed meanwhile (an options toggle): keep that one.
        if (this.edgeMesh) {
            built.geometry.dispose();
            return this.edgeMesh;
        }
        this.assembleEdgeOverlay(renderer, built);
        return this.edgeMesh!;
    }

    private assembleEdgeOverlay(
        renderer: THREE.WebGLRenderer,
        {geometry, rangeIdToIndex}: {geometry: THREE.BufferGeometry; rangeIdToIndex: Map<string, number>},
    ): void {
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
        // Ranges hidden before the overlay existed -- it arrives late on a load -- stay hidden.
        if (this.hiddenRanges.size) {
            const data = (this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture).image.data as Uint8Array;
            for (const id of this.hiddenRanges) {
                const idx = rangeIdToIndex.get(id);
                if (idx !== undefined) data[idx] = 0;
            }
            (this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture).needsUpdate = true;
        }
        this.edgeMesh = new THREE.LineSegments(geometry, this.edgeMaterial);
        this.edgeMesh.layers.set(1);
        // A live rebuild (refreshEdgeOverlays) lands after the section planes were
        // applied; seed it so a toggle does not un-clip the edges until a plane moves.
        clipWithModel(this.edgeMesh);
        // now *after* you’ve extracted the lines, bake the transform:
        this.edgeMesh.applyMatrix4(localMat);
    }

    /** Drop the cached edge overlay so the next getEdgeOverlay() rebuilds it from the CURRENT options
     *  (e.g. after hideTessellationEdges toggled feature-only vs full-triangulation edges). Returns the
     *  old LineSegments so the caller can remove it from the scene before re-adding the rebuilt one. */
    public invalidateEdgeOverlay(): THREE.LineSegments | undefined {
        this.edgeGeneration++; // an async build in flight is for the options being replaced
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

        // FEA line elements have no triangles, so they own no draw range and
        // nothing above highlights them. When a result field has installed a
        // line renderer it leaves a callback here to highlight its own
        // segments -- a generic hook, so this class stays unaware of FEA.
        const lineSelection = this.userData?.__feaLineSelection as
            | ((ids: readonly string[]) => void)
            | undefined;
        if (lineSelection) lineSelection(rangeIds);

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

    // ── Colours the user gave objects (ViewportApi.setObjectColors) ──────────────────────────
    //
    // A colour per draw range, drawn through the one colour attribute of the mesh: no material and
    // no draw call per object. While any range has one, the base material is white and every
    // vertex carries its colour (the mesh's own, or the user's), so a recoloured object is exactly
    // the colour asked for, lit by the mesh's material. Hiding, isolating and section planes do
    // not look at colours, so they keep them; the selection is drawn by its overlay, as on any
    // mesh coloured by vertex.

    /** Per sorted segment: 0 for the mesh's own colour, else 0xff000000 | 0xRRGGBB (sRGB). */
    private _userColors: Uint32Array | null = null;
    private _userColorCount = 0;
    /** The base material's colour while the user's colours have turned the material white. */
    private _ownColor: THREE.Color | null = null;
    private _segIndexById?: Map<string, number>;

    /** How many draw ranges have a colour the user gave them. */
    public get userColorCount(): number {
        return this._userColorCount;
    }

    /** Whether the user's colours can be drawn on this mesh: not while its vertex colours are
     *  someone else's (a painted FEA result, a file's own vertex colours, a colouring by property). */
    public acceptsUserColors(): boolean {
        return this._ownColor !== null || !this.geometry.getAttribute('color');
    }

    /** The colour the user gave range `id` (0xff000000 | 0xRRGGBB), or 0 for its own. */
    public userColorOf(id: string): number {
        const i = this._userColors ? this.segmentIndex().get(id) : undefined;
        return i === undefined ? 0 : this._userColors![i];
    }

    private segmentIndex(): Map<string, number> {
        if (!this._segIndexById) {
            const ids = this.getSortedSegments().ids;
            const map = new Map<string, number>();
            for (let i = 0; i < ids.length; i++) map.set(ids[i], i);
            this._segIndexById = map;
        }
        return this._segIndexById;
    }

    /** Give `ids` (every range when null) the colour `packed` (0xRRGGBB, sRGB), or their own
     *  colour back when it is null. One pass over the ranges named, nothing allocated per range.
     *  Returns how many ranges changed. */
    public setUserColors(ids: Iterable<string> | null, packed: number | null): number {
        if (!this.acceptsUserColors() || !this.geometry.index) return 0;
        const value = packed === null ? 0 : (0xff000000 | (packed & 0xffffff)) >>> 0;
        if (value === 0 && this._userColorCount === 0) return 0;
        const segs = this.getSortedSegments();
        const colors = (this._userColors ??= new Uint32Array(segs.ids.length));
        const before = this._userColorCount;
        const writes = this.beginUserPaint();
        let changed = 0;
        const paint = (i: number) => {
            const was = colors[i];
            if (was === value) return;
            colors[i] = value;
            if (was === 0) this._userColorCount++;
            else if (value === 0) this._userColorCount--;
            changed++;
            this.paintSegment(writes, segs.starts[i], segs.counts[i], value);
        };
        if (ids === null) {
            for (let i = 0; i < colors.length; i++) paint(i);
        } else {
            const index = this.segmentIndex();
            for (const id of ids) {
                const i = index.get(id);
                if (i !== undefined) paint(i);
            }
        }
        this.endUserPaint(before, changed > 0);
        return changed;
    }

    /** The user's colours as they are now, for the undo history; null when there are none. */
    public userColorSnapshot(): Uint32Array | null {
        return this._userColorCount > 0 ? this._userColors!.slice() : null;
    }

    /** Put back what `userColorSnapshot` gave. A snapshot of another number of ranges is ignored. */
    public restoreUserColors(snapshot: Uint32Array | null): void {
        if (!this.acceptsUserColors() || !this.geometry.index) return;
        if (!snapshot && this._userColorCount === 0) return;
        const segs = this.getSortedSegments();
        if (snapshot && snapshot.length !== segs.ids.length) return;
        const colors = (this._userColors ??= new Uint32Array(segs.ids.length));
        const before = this._userColorCount;
        const writes = this.beginUserPaint();
        let changed = false;
        for (let i = 0; i < colors.length; i++) {
            const value = snapshot ? snapshot[i] : 0;
            const was = colors[i];
            if (was === value) continue;
            colors[i] = value;
            if (was === 0) this._userColorCount++;
            else if (value === 0) this._userColorCount--;
            changed = true;
            this.paintSegment(writes, segs.starts[i], segs.counts[i], value);
        }
        this.endUserPaint(before, changed);
    }

    /** The mesh's own colour (linear RGB): what its base material has, also while the user's
     *  colours have turned that white. */
    public setOwnColor(r: number, g: number, b: number): void {
        if (!this._ownColor) {
            (this.originalMaterial as THREE.Material & {color?: THREE.Color}).color?.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
            return;
        }
        this._ownColor.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
        this.repaintUserColors();
    }

    /** The colour attribute the user's colours are written to, made (every vertex in the mesh's
     *  own colour, the material white) the first time. */
    private beginUserPaint(): Float32Array {
        const existing = this.geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
        if (this._ownColor && existing) return existing.array as Float32Array;
        const mat0 = this.originalMaterial as THREE.Material & {color?: THREE.Color; vertexColors?: boolean};
        const own = (this._ownColor = mat0.color ? mat0.color.clone() : new THREE.Color(1, 1, 1));
        const pos = this.geometry.getAttribute('position');
        const arr = new Float32Array(pos.count * 3);
        fillVertexColors(arr, own);
        this.geometry.setAttribute('color', new THREE.BufferAttribute(arr, 3));
        mat0.color?.setRGB(1, 1, 1);
        mat0.vertexColors = true;
        mat0.needsUpdate = true;
        return arr;
    }

    /** One range's vertices in `value` (0: the mesh's own colour). */
    private paintSegment(arr: Float32Array, start: number, count: number, value: number): void {
        paintIndexedRange(arr, this.geometry.index!.array, start, count, rangeRgb(value, this._ownColor!));
    }

    /** After a paint: upload what changed, and enter or leave the per-vertex look. */
    private endUserPaint(countBefore: number, changed: boolean): void {
        if (this._userColorCount === 0) {
            this.leaveUserPaint();
            return;
        }
        if (changed) (this.geometry.getAttribute('color') as THREE.BufferAttribute).needsUpdate = true;
        if (countBefore === 0) {
            // The selection is drawn by its overlay from here on, as on any mesh coloured by vertex.
            this._recomputeUsesVertexColorsFlag();
            this.reapplySelectionHighlight();
        }
    }

    /** No range has a user colour any more: the mesh is drawn by its material's colour again. */
    private leaveUserPaint(): void {
        const own = this._ownColor;
        if (!own) return;
        this._ownColor = null;
        this._userColors = null;
        const mat0 = this.originalMaterial as THREE.Material & {color?: THREE.Color; vertexColors?: boolean};
        mat0.color?.copy(own);
        this.geometry.deleteAttribute('color');
        this._baseColors = undefined;
        mat0.vertexColors = false;
        mat0.needsUpdate = true;
        this._recomputeUsesVertexColorsFlag();
        this._disposeSelectionOverlay();
        this.reapplySelectionHighlight();
    }

    /** Every vertex again: its mesh's own colour, then the user's colours over it. */
    private repaintUserColors(): void {
        const attr = this.geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
        const colors = this._userColors;
        if (!attr || !colors || !this._ownColor) return;
        const arr = attr.array as Float32Array;
        fillVertexColors(arr, this._ownColor);
        const segs = this.getSortedSegments();
        for (let i = 0; i < colors.length; i++) {
            if (colors[i]) this.paintSegment(arr, segs.starts[i], segs.counts[i], colors[i]);
        }
        attr.needsUpdate = true;
    }

    /** Disable vertex colors and restore original material behavior for non-vertex-colored mode */
    public disableVertexColorsAndResetMaterial(): void {
        // A feature's own colouring (by property, by system) goes; the colours the user gave
        // objects stay, drawn again over the mesh's own.
        if (this._ownColor && this._userColorCount > 0) {
            this.repaintUserColors();
            return;
        }
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
        this.edgeGeneration++; // an overlay still being built must not attach to a disposed mesh
        this._disposeSelectionOverlay();
        this.clearFaceHighlight();
        this.setMarks(null);
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
        // this.geometry IS originalGeometry, and this mesh owns it:
        // convert_to_custom_batch_mesh hands over the loader's geometry when this
        // is its first user and a clone() of it when another CustomBatchedMesh
        // already claimed it, so disposing here never pulls buffers from under a
        // sibling mesh.
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
            followMorph(srcGeom, overlayGeom);

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

    /** Draw the ranges of `marks` (range id -> colour, 0xRRGGBB sRGB) in their colours, unlit, over
     *  the model and its selection, until the next call; null or an empty map takes them away. Not the
     *  selection and not a colour of the object's own: neither changes, and nothing is recorded. A
     *  hidden range is not drawn. */
    public setMarks(marks: ReadonlyMap<string, number> | null): void {
        // Kept, so a later hide or show draws the marks again without the hidden ones (`updateGroups`).
        this._marks = marks;
        const entries = marks ? [...marks].filter(([id]) => this.drawRanges.has(id) && !this.hiddenRanges.has(id)) : [];
        const old = this._markOverlay;
        if (old) for (const m of old.material as THREE.Material[]) m.dispose();
        if (!entries.length) {
            if (old) this.remove(old);
            this._markOverlay = undefined;
            return;
        }
        const colours = [...new Set(entries.map(([, c]) => c))];
        const materials = colours.map((c) => {
            const mat = new THREE.MeshBasicMaterial({color: c});
            prepareOverlayMaterial(mat);
            // In front of the selection's overlay, which sits at -1.
            mat.polygonOffsetFactor = -2;
            mat.polygonOffsetUnits = -2;
            // In the transparent pass, after the selection (see `_rebuildSelectionOverlay`).
            mat.transparent = true;
            return mat;
        });
        let overlay = old;
        if (!overlay) {
            overlay = new THREE.Mesh(sharedOverlayGeometry(this.geometry as THREE.BufferGeometry), materials);
            overlay.matrixAutoUpdate = true;
            overlay.layers.mask = this.layers.mask;
            overlay.renderOrder = 9;
            // Known as this mesh's own before it is added: `add` leaves its own overlays alone.
            this._markOverlay = overlay;
            this.add(overlay);
        }
        overlay.material = materials;
        (overlay as any).morphTargetInfluences = (this as any).morphTargetInfluences;
        (overlay as any).morphTargetDictionary = (this as any).morphTargetDictionary;
        const geom = overlay.geometry as THREE.BufferGeometry;
        geom.clearGroups();
        for (const [id, c] of entries) {
            const [start, count] = this.drawRanges.get(id)!;
            geom.addGroup(start, count, colours.indexOf(c));
        }
    }

    private _rebuildSelectionOverlay(rangeIds: string[]): void {
        // Nothing selected: PARK the overlay rather than dispose it. A plain click clears the
        // selection and then selects, so disposing here threw the overlay geometry away on every
        // click -- and three.js keys a mesh's morph texture by its geometry, so the next selection
        // rebuilt a texture the size of the whole mesh. On a beam-solid mesh (millions of
        // vertices) that is the click that hung the viewer.
        if (!rangeIds || rangeIds.length === 0) {
            if (this._selectionOverlay) {
                this._selectionOverlay.visible = false;
                ((this._selectionOverlay as THREE.Mesh).geometry as THREE.BufferGeometry).clearGroups();
            }
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
            this._selectionOverlay.visible = true;
            overlayGeom = (this._selectionOverlay as THREE.Mesh).geometry as THREE.BufferGeometry;
            overlayMat = (this._selectionOverlay as THREE.Mesh).material as THREE.Material;
            // Ensure overlay is referencing base attributes; if not, rebuild references
            if (!overlayGeom.getAttribute('position') || overlayGeom.getAttribute('position') !== srcGeom.getAttribute('position')) {
                overlayGeom = sharedOverlayGeometry(srcGeom);
                (this._selectionOverlay as THREE.Mesh).geometry = overlayGeom;
            } else if (overlayGeom.morphAttributes.position !== srcGeom.morphAttributes.position) {
                // The mesh got a new morph without a refreshMorphTexture (which would
                // have re-pointed this overlay): catch up, or it draws the old warp.
                overlayGeom.morphAttributes = {...srcGeom.morphAttributes};
                overlayGeom.morphTargetsRelative = srcGeom.morphTargetsRelative === true;
                refreshMorphTexture(overlayGeom);
            }
            // Ensure material is configured for GPU morphing and polygon offset
            prepareOverlayMaterial(overlayMat);
        } else {
            overlayGeom = sharedOverlayGeometry(srcGeom);

            overlayMat = selectedMaterial.clone();
            prepareOverlayMaterial(overlayMat);

            this._selectionOverlay = new THREE.Mesh(overlayGeom, overlayMat);
            this._selectionOverlay.matrixAutoUpdate = true;
            this._selectionOverlay.layers.mask = this.layers.mask;
            this.add(this._selectionOverlay);
        }

        // Build overlay materials as an array so Three.js respects geometry groups
        const visibleSelMat = overlayMat instanceof Array ? (overlayMat[0] as THREE.Material) : overlayMat;
        // The overlay's own from a previous selection when it has one: a new material per
        // click was one more allocation per click and never disposed.
        const invisibleMat =
            overlayMat instanceof Array && overlayMat[1]
                ? (overlayMat[1] as THREE.Material)
                : new THREE.MeshBasicMaterial({ visible: false });
        // Ensure morphTargets on both
        (visibleSelMat as any).morphTargets = true;
        (invisibleMat as any).morphTargets = true;
        // Reduce z-fighting for the visible overlay
        (visibleSelMat as any).polygonOffset = true;
        (visibleSelMat as any).polygonOffsetFactor = -1;
        (visibleSelMat as any).polygonOffsetUnits = -1;

        // Assign material array to overlay
        (this._selectionOverlay as THREE.Mesh).material = [visibleSelMat, invisibleMat];

        // Draw the selection highlight in the TRANSPARENT pass, above field /
        // plugin face overlays. `selectedMaterial` is opaque, so by default the
        // highlight renders in the opaque pass — and three.js draws the whole
        // transparent pass afterwards, so any transparent overlay on the same
        // geometry (e.g. a utilisation-colour face overlay at renderOrder 2)
        // occludes the highlight regardless of renderOrder. Flagging it
        // transparent (opacity is still 1.0, so the colour is unchanged) puts it in
        // the same pass, and the high renderOrder makes it sort last so the
        // highlight is always visible. depthTest stays on (polygonOffset above
        // prevents z-fight), so it's still hidden behind solid geometry in front.
        (visibleSelMat as THREE.Material).transparent = true;
        (this._selectionOverlay as THREE.Mesh).renderOrder = 8;

        // Make overlay follow the same morph targets as the base mesh
        (this._selectionOverlay as any).morphTargetInfluences = (this as any).morphTargetInfluences;
        (this._selectionOverlay as any).morphTargetDictionary = (this as any).morphTargetDictionary;

        // Rebuild groups to cover the entire index range, using materialIndex 0 for selected, 1 for others/hidden
        overlayGeom.clearGroups();
        const idxAttr = srcGeom.getIndex();
        const idxCount = idxAttr ? idxAttr.count : (srcGeom.attributes.position?.count ?? 0);
        // The cached sort (as updateGroups uses), not a fresh sort of every range on every click.
        const segs = this.getSortedSegments();

        // Selected (and not hidden) ranges show; gaps and everything else are invisible. Neighbouring
        // ranges of one material share a group, so a large selection is a few draws, not one per part.
        const selectedSet = new Set(rangeIds);
        for (const g of coalescedGroups(
            segs.starts,
            segs.counts,
            (i) => (!this.hiddenRanges.has(segs.ids[i]) && selectedSet.has(segs.ids[i]) ? 0 : 1),
            idxCount,
            1,
        )) {
            overlayGeom.addGroup(g.start, g.count, g.materialIndex);
        }

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
        const influences: number[] | undefined = this.morphTargetInfluences;
        const overlayGeom = this._selectionOverlay.geometry as THREE.BufferGeometry;
        const oPosAttr = overlayGeom.getAttribute('position') as THREE.BufferAttribute | undefined;
        if (!oPosAttr) return;

        const arr = oPosAttr.array as Float32Array;
        const srcIdx = this._overlaySourceIndices;
        const tmp = new THREE.Vector3();
        for (let i = 0, ov = 0; i < srcIdx.length; i++, ov += 3) {
            const vi = srcIdx[i];
            tmp.set(posAttr.getX(vi), posAttr.getY(vi), posAttr.getZ(vi));
            applyMorphTargets(tmp, vi, morphPositions, influences, morphTargetsRelative);
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
        this.markHiddenChanged();

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

    /** The inverse of ``hideBatchDrawRange``: show these draw ranges again, leaving every other
     *  hidden range hidden. */
    public unhideBatchDrawRange(rangeIds: Iterable<string>): void {
        const shown: string[] = [];
        for (const id of rangeIds) {
            if (this.hiddenRanges.delete(id)) shown.push(id);
        }
        if (shown.length === 0) return;
        this.markHiddenChanged();
        this.updateGroups();
        if (this.edgeMaterial && this.rangeIdToIndex) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            const data = tex.image.data as Uint8Array;
            for (const id of shown) {
                const idx = this.rangeIdToIndex.get(id);
                if (idx !== undefined) data[idx] = 255;
            }
            tex.needsUpdate = true;
        }
    }

    public getHiddenRanges(): ReadonlySet<string> {
        return this.hiddenRanges;
    }

    /**
     * Hide everything EXCEPT ``keep`` -- the isolation the clash panels ask for when a joint is
     * focused ("show me this joint, not the frame around it").
     *
     * Expressed as one call rather than "collect every other id, then hideBatchDrawRange" because
     * the complement is this mesh's own knowledge (``drawRanges`` holds every id it owns) and the
     * caller would otherwise have to materialise a set of tens of thousands of strings to say
     * "the rest". Replaces the hidden set rather than adding to it, so isolating twice in a row
     * is idempotent and moving the cursor to the next joint does not accumulate.
     */
    public isolateDrawRanges(keep: ReadonlySet<string>): void {
        this.hiddenRanges.clear();
        for (const id of this.drawRanges.keys()) {
            if (!keep.has(id)) this.hiddenRanges.add(id);
        }
        this.markHiddenChanged();
        this.updateGroups();
        if (this.edgeMaterial && this.rangeIdToIndex) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            const data = tex.image.data as Uint8Array;
            for (const [id, idx] of this.rangeIdToIndex) {
                data[idx] = this.hiddenRanges.has(id) ? 0 : 255;
            }
            tex.needsUpdate = true;
        }
    }

    /**
     * What a HIDDEN range looks like: gone, or a translucent ghost of itself.
     *
     * Hidden ranges already route to material slot 2 (see ``updateGroups``), so "hide" and
     * "fade the rest" are the same mechanism with a different material -- which is why this
     * reconfigures the cached instance instead of introducing a fourth slot and a second set of
     * per-range bookkeeping that could disagree with the first.
     *
     * ``depthWrite: false`` on the ghost: a translucent member that still wrote depth would
     * occlude the joint it is meant to be showing THROUGH it.
     */
    public setHiddenAppearance(mode: "invisible" | "ghost", opacity = 0.15): void {
        const mat = this._matInvisible as THREE.MeshBasicMaterial;
        if (mode === "invisible") {
            mat.visible = false;
            mat.transparent = false;
            mat.opacity = 1;
            mat.depthWrite = true;
        } else {
            mat.visible = true;
            mat.transparent = true;
            mat.opacity = Math.max(0.01, Math.min(1, opacity));
            mat.depthWrite = false;
            mat.color.set(0x8a8f98);
        }
        mat.needsUpdate = true;
    }

    public unhideAllDrawRanges() {
        this.hiddenRanges.clear();
        this.markHiddenChanged();
        this.updateGroups();
        if (this.edgeMaterial) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            (tex.image.data as Uint8Array).fill(255);
            this.edgeMaterial.uniforms.uHighlighted.value = -1;
            tex.needsUpdate = true;
        }
    }

    /**
     * Overrides the raycast method to ignore hidden draw ranges (`./customBatchedMesh/raycast`).
     */
    raycast(raycaster: THREE.Raycaster, intersects: THREE.Intersection[]): void {
        raycastBatchedMesh(this, raycaster, intersects);
    }
}
