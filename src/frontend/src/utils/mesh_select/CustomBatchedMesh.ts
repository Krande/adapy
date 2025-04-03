// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials'; // Adjust the import path as needed

export class CustomBatchedMesh extends THREE.Mesh {
    // Original geometry and material
    originalGeometry: THREE.BufferGeometry;
    originalMaterial: THREE.Material | THREE.Material[];

    // Map of draw range IDs to their [start, count] in the index buffer
    drawRanges: Map<string, [number, number]>;

    // Map of draw range IDs to their corresponding materials
    materialsMap: Map<string, THREE.Material>;

    // Set of currently highlighted draw range IDs
    highlightedDrawRanges: Set<string>;

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
        material: THREE.Material | THREE.Material[],
        drawRanges: Map<string, [number, number]>
    ) {
        // Create a clone of the geometry to avoid modifying the original
        const clonedGeometry = geometry.clone();

        // Initialize the original geometry and material
        super(clonedGeometry, material);

        this.originalGeometry = geometry;
        this.originalMaterial = material;
        this.drawRanges = drawRanges;
        this.materialsMap = new Map();
        this.highlightedDrawRanges = new Set();

        // Clear existing groups in the geometry
        this.geometry.clearGroups();

        // Initialize materials and geometry groups
        this.initializeMaterialsAndGroups();
    }

    /**
     * Initializes materials and geometry groups for each draw range.
     */
    private initializeMaterialsAndGroups(): void {
        let materialIndex = 0;
        const materialsArray: THREE.Material[] = [];

        // Iterate over the draw ranges and set up materials and groups
        this.drawRanges.forEach((range, rangeId) => {
            const [start, count] = range;

            // Add a group for this draw range
            this.geometry.addGroup(start, count, materialIndex);

            // Clone the original material for this draw range
            const rangeMaterial = (Array.isArray(this.originalMaterial)
                    ? this.originalMaterial[0]
                    : this.originalMaterial
            ).clone();

            // Optional: Set an identifier for the material (useful for debugging)
            (rangeMaterial as any).rangeId = rangeId;

            // Add the material to the materials array
            materialsArray.push(rangeMaterial);

            // Map the range ID to the material
            this.materialsMap.set(rangeId, rangeMaterial);

            materialIndex++;
        });

        // Assign the materials array to the mesh
        this.material = materialsArray;
    }

    /**
     * Hides the specified draw range by setting its material's visibility to false.
     * @param drawRangeId The ID of the draw range to hide.
     */
    hideDrawRange(drawRangeId: string): void {
        const material = this.materialsMap.get(drawRangeId);
        if (material) {
            material.visible = false;
        } else {
            console.warn(`Material for draw range ID ${drawRangeId} not found.`);
        }
    }

    /**
     * Unhides all draw ranges by setting all materials' visibility to true.
     */
    unhideAllDrawRanges(): void {
        this.materialsMap.forEach((material) => {
            material.visible = true;
        });
    }

    /**
     * Highlights the specified draw ranges by replacing their materials with the selected material.
     * @param drawRangeIds An array of draw range IDs to highlight.
     */
    highlightDrawRanges(drawRangeIds: string[]): void {
        // Clear previous highlights
        this.clearHighlights();

        // Keep track of highlighted draw ranges
        this.highlightedDrawRanges = new Set(drawRangeIds);

        drawRangeIds.forEach((rangeId) => {
            const materialIndex = this.getMaterialIndexByRangeId(rangeId);
            if (materialIndex !== -1) {
                // Replace the material with the selected material
                (this.material as THREE.Material[])[materialIndex] = selectedMaterial;
            } else {
                console.warn(`Material index for draw range ID ${rangeId} not found.`);
            }
        });
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
        console.log('CustomBatchedMesh raycast called');
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
