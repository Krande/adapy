// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials';
import {buildEdgeGeometryWithRangeIds, makeEdgeShaderMaterial} from './EdgeShaderHelper';
import {DesignDataExtension, SimulationDataExtensionMetadata} from "../../extensions/design_and_analysis_extension";


export class CustomBatchedMesh extends THREE.Mesh {
    originalGeometry: THREE.BufferGeometry;
    originalMaterial: THREE.Material;
    drawRanges: Map<string, [number, number]>;

    public readonly unique_key: string;
    public readonly is_design: boolean;
    public readonly ada_ext_data: SimulationDataExtensionMetadata | DesignDataExtension | null;

    private selectedRanges = new Set<string>();
    private hiddenRanges = new Set<string>();

    private edgeMesh?: THREE.LineSegments;
    private rangeIdToIndex?: Map<string, number>;
    private edgeMaterial?: THREE.ShaderMaterial;

    // Selection coloring helpers
    private _usesVertexColorsFlag: boolean = false;
    private _baseColors?: Float32Array; // snapshot of current animated/base colors
    private _selectionOverlay?: THREE.Mesh;
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
        super(geometry.clone(), material.clone());
        this.originalGeometry = geometry;
        this.originalMaterial = material.clone();
        this.drawRanges = drawRanges;
        this.unique_key = unique_key;
        this.is_design = is_design;
        this.ada_ext_data = ada_ext_data;

        // Determine if this mesh uses vertex colors initially
        this._recomputeUsesVertexColorsFlag();
        this.updateGroups();
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
        this.material = [
            this.originalMaterial,
            selectedMaterial.clone(),
            new THREE.MeshBasicMaterial({visible: false})
        ];
        const segs = Array.from(this.drawRanges.entries())
            .map(([id, [s, c]]) => ({id, s, c}))
            .sort((a, b) => a.s - b.s);

        let cur = 0;
        for (const {id, s, c} of segs) {
            if (s > cur) this.geometry.addGroup(cur, s - cur, 0);
            let mi: 0 | 1 | 2 = 0;
            if (this.hiddenRanges.has(id)) mi = 2;
            else if (this.selectedRanges.has(id)) mi = (this._usesVertexColorsFlag ? 0 : 1);
            this.geometry.addGroup(s, c, mi);
            cur = s + c;
        }
        if (cur < idxCount) this.geometry.addGroup(cur, idxCount - cur, 0);
    }

    /** call this when you have a renderer and want the overlay in the scene */
    public getEdgeOverlay(renderer: THREE.WebGLRenderer): THREE.LineSegments {
        if (!this.edgeMesh) {
            // first‐time initialization
            const {geometry, rangeIdToIndex} =
                buildEdgeGeometryWithRangeIds(this.originalGeometry, this.drawRanges);
            this.rangeIdToIndex = rangeIdToIndex;
            this.updateWorldMatrix(true, false);
            const worldMat = this.matrixWorld;

            this.edgeMaterial = makeEdgeShaderMaterial(renderer, rangeIdToIndex.size);
            this.edgeMesh = new THREE.LineSegments(geometry, this.edgeMaterial);
            this.edgeMesh.layers.set(1);
            // now *after* you’ve extracted the lines, bake the transform:
            this.edgeMesh.applyMatrix4(worldMat);
        }
        return this.edgeMesh;
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

        // Edge overlay highlight (unchanged)
        if (this.edgeMaterial && this.rangeIdToIndex) {
            this.edgeMaterial.uniforms.uHighlighted.value =
                rangeIds.length ? this.rangeIdToIndex.get(rangeIds[0])! : -1;
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

        // 2) Rebuild all groups just once
        this.updateGroups();

        // 3) Update the edge-overlay texture in one shot
        if (this.edgeMaterial && this.rangeIdToIndex) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            for (const id of rangeIds) {
                const idx = this.rangeIdToIndex.get(id);
                if (idx !== undefined) {
                    tex.image.data[idx] = 0;
                }
            }
            tex.needsUpdate = true;
        }
    }

    public unhideAllDrawRanges() {
        this.hiddenRanges.clear();
        this.updateGroups();
        if (this.edgeMaterial) {
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            tex.image.data.fill(255);
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
        group: { start: number; count: number; materialIndex: number },
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
        materialIndex: number
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
            const uvA = this._raycast_uvA.fromBufferAttribute(uvAttribute, a);
            const uvB = this._raycast_uvB.fromBufferAttribute(uvAttribute, b);
            const uvC = this._raycast_uvC.fromBufferAttribute(uvAttribute, c);

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
        uvA.multiplyScalar(barycoord.x);
        uvB.multiplyScalar(barycoord.y);
        uvC.multiplyScalar(barycoord.z);
        uv.add(uvA).add(uvB).add(uvC);
        return uv;
    }
}
