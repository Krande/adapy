// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials'; // Adjust the import path as needed

export class CustomBatchedMesh extends THREE.Mesh {
    // Original geometry and material
    originalGeometry: THREE.BufferGeometry;
    originalMaterial: THREE.Material;

    // Map of draw range IDs to their [start, count] in the index buffer
    drawRanges: Map<string, [number, number]>;

    // Map of draw range IDs to their corresponding materials
    materialsMap: Map<string, THREE.Material>;

    // Set of currently highlighted draw range IDs
    highlightedDrawRanges: Set<string>;

    // currently selected ranges
    private selectedRanges = new Set<string>();
    private hiddenRanges = new Set<string>();

    public get_edge_lines(): THREE.LineSegments {
        // Create edges geometry and add it as a line segment
        const edges = new THREE.EdgesGeometry(this.geometry);
        const lineMaterial = new THREE.LineBasicMaterial({color: 0x000000});
        const edgeLine = new THREE.LineSegments(edges, lineMaterial);

        // Ensure the edge line inherits transformations
        edgeLine.position.copy(this.position);
        edgeLine.rotation.copy(this.rotation);
        edgeLine.scale.copy(this.scale);
        edgeLine.layers.set(1);

        return edgeLine
    }

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
        drawRanges: Map<string, [number, number]>
    ) {
        // clone geometry to avoid mutating the source
        const geo = geometry.clone();
        super(geo, material.clone());

        this.originalGeometry = geometry;
        this.originalMaterial = material.clone();
        this.drawRanges = drawRanges;

        // initial grouping (no selection, no hidden)
        this.updateGroups();
    }

    /**
     * Rebuild geometry.groups & material array based on selected/hidden sets.
     * This is the shared workhorse that both hide() and unhide() call.
     */
    private updateGroups(): void {
        const idxAttr = this.geometry.index!;
        const totalCount = idxAttr.count;
        this.geometry.clearGroups();

        // 0: baseMat, 1: highlightMat, 2: hiddenMat
        const baseMat = this.originalMaterial;
        const highlightMat = selectedMaterial.clone();
        const hiddenMat = new THREE.MeshBasicMaterial({visible: false});
        const mats = [baseMat, highlightMat, hiddenMat] as THREE.Material[];

        // Build segments for *every* range, choosing the right slot:
        type Seg = { id: string; start: number; count: number };
        const segs: Seg[] = [];
        for (const [id, [start, count]] of this.drawRanges) {
            segs.push({id, start, count});
        }
        segs.sort((a, b) => a.start - b.start);

        let cursor = 0;
        for (const seg of segs) {
            if (seg.start > cursor) {
                // any “gap” triangles that aren’t in drawRanges:
                this.geometry.addGroup(cursor, seg.start - cursor, 0);
            }
            let matIndex: 0 | 1 | 2 = 0;
            if (this.hiddenRanges.has(seg.id)) matIndex = 2;
            else if (this.selectedRanges.has(seg.id)) matIndex = 1;
            this.geometry.addGroup(seg.start, seg.count, matIndex);
            cursor = seg.start + seg.count;
        }
        if (cursor < totalCount) {
            this.geometry.addGroup(cursor, totalCount - cursor, 0);
        }

        this.material = mats;
    }


    /**
     * Highlight the given draw ranges (blue) and redraw groups.
     */
    public updateSelectionGroups(rangeIds: string[]): void {
        this.selectedRanges = new Set(rangeIds);
        this.updateGroups();
    }

    /**
     * Clear all selection highlighting.
     */
    public clearSelectionGroups(): void {
        this.selectedRanges.clear();
        this.updateGroups();
    }

    /**
     * Hide the specified draw range (removes it from rendering).
     */
    /**
     * Hide the specified draw range (removes it from both rendering and click)
     */
    public hideDrawRange(rangeId: string): void {
        this.hiddenRanges.add(rangeId);
        this.updateGroups();
    }

    /**
     * Unhide all draw ranges (restores full rendering and click)
     */
    public unhideAllDrawRanges(): void {
        this.hiddenRanges.clear();
        this.updateGroups();
    }

    /**
     * Return only the raw edge geometry for merging elsewhere.
     */
    public get_edge_geometry(): THREE.BufferGeometry {
        const edgesGeo = new THREE.EdgesGeometry(this.geometry);
        this.updateWorldMatrix(true, false);
        edgesGeo.applyMatrix4(this.matrixWorld);
        return edgesGeo;
    }

    /**
     * Clears all highlights by restoring the original materials.
     */
    private clearHighlights(): void {
        this.highlightedDrawRanges.forEach((rangeId) => {
            const originalMaterial = this.materialsMap.get(rangeId);
            const materialIndex = this.getMaterialIndexByRangeId(rangeId);
            if (originalMaterial && materialIndex !== -1) {
                // Restore the original material
                (this.material as THREE.Material[])[materialIndex] = originalMaterial;
            }
        });
        this.highlightedDrawRanges.clear();
    }

    /**
     * Deselects all draw ranges by clearing highlights.
     */
    deselect(): void {
        this.clearHighlights();
    }

    /**
     * Gets the material index corresponding to the specified draw range ID.
     * @param rangeId The draw range ID.
     * @returns The material index or -1 if not found.
     */
    private getMaterialIndexByRangeId(rangeId: string): number {
        const materialKeys = Array.from(this.materialsMap.keys());
        return materialKeys.indexOf(rangeId);
    }

    /**
     * Overrides the raycast method to ignore hidden draw ranges.
     */
    raycast(raycaster: THREE.Raycaster, intersects: THREE.Intersection[]): void {
        //console.log('CustomBatchedMesh raycast called');
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

        if (position === undefined) return;

        const start = group.start;
        const end = start + group.count;

        const vA = this._raycast_vA;
        const vB = this._raycast_vB;
        const vC = this._raycast_vC;

        let intersection;

        if (index !== null) {
            // Indexed geometry
            const indices = index.array as Uint16Array | Uint32Array;

            for (let i = start; i < end; i += 3) {
                const a = indices[i];
                const b = indices[i + 1];
                const c = indices[i + 2];

                intersection = this.checkBufferGeometryIntersection(
                    this,
                    material,
                    raycaster,
                    localRay,
                    position,
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

                intersection = this.checkBufferGeometryIntersection(
                    this,
                    material,
                    raycaster,
                    localRay,
                    position,
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
        position: THREE.BufferAttribute,
        a: number,
        b: number,
        c: number,
        materialIndex: number
    ): THREE.Intersection | null {
        const vA = this._raycast_vA;
        const vB = this._raycast_vB;
        const vC = this._raycast_vC;
        const intersectionPoint = this._raycast_intersectionPoint;

        vA.fromBufferAttribute(position, a);
        vB.fromBufferAttribute(position, b);
        vC.fromBufferAttribute(position, c);

        let side = material.side;

        if (side === undefined) side = THREE.FrontSide;

        const backfaceCulling = side === THREE.FrontSide;

        const intersect = ray.intersectTriangle(
            vC,
            vB,
            vA,
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
            uv = this._uvIntersection(vA, vB, vC, uvA, uvB, uvC, intersectionPoint);
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
