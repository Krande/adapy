// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials';
import {mergeGeometries} from "three/examples/jsm/utils/BufferGeometryUtils.js";
import {useModelStore} from "../../state/modelStore"; // Adjust the import path as needed

export class CustomBatchedMesh extends THREE.Mesh {
    // Original geometry and material
    originalGeometry: THREE.BufferGeometry;
    originalMaterial: THREE.Material;

    // Map of draw range IDs to their [start, count] in the index buffer
    drawRanges: Map<string, [number, number]>;

    // currently selected ranges
    private selectedRanges = new Set<string>();
    private hiddenRanges = new Set<string>();
    private perRangeEdgeGeoms = new Map<string, THREE.BufferGeometry>();
    private edgeMesh: THREE.LineSegments | null = null;

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

        if (useModelStore.getState().should_hide_edges){
            this.updateVisibleEdges(); // new
        }
    }

    /**
     * Unhide all draw ranges (restores full rendering and click)
     */
    public unhideAllDrawRanges(): void {
        this.hiddenRanges.clear();
        this.updateGroups();
        if (useModelStore.getState().should_hide_edges){
            this.updateVisibleEdges(); // new
        }
    }

    /**
     * Call this once you add the mesh to your scene, so we can build per-range
     * edge geoms and insert the merged-edge mesh.
     */
    public initEdgeOverlay() {
        // 1) Build per-range edge buffer geometries
        this.drawRanges.forEach(([start, count], rangeId) => {
            // slice that drawRange out of the main index
            const idxArr = (this.geometry.index!.array as Uint16Array | Uint32Array).slice(
                start, start + count
            );

            const subGeo = new THREE.BufferGeometry();
            // reuse the position attribute
            subGeo.setAttribute('position', this.geometry.attributes.position);
            subGeo.setIndex(Array.from(idxArr));

            // compute edges just for that piece
            this.perRangeEdgeGeoms.set(rangeId, new THREE.EdgesGeometry(subGeo));
        });

        // 2) create the merged initial edgeMesh
        const merged = mergeGeometries(
            Array.from(this.perRangeEdgeGeoms.values()), false
        )!;
        const mat = new THREE.LineBasicMaterial({color: 0x000000});
        this.edgeMesh = new THREE.LineSegments(merged, mat);
        this.edgeMesh.layers.set(1);

        return this.edgeMesh
    }

    /**
     * Re‐merge only the edges *not* hidden; call on hide/unhide.
     */
    private updateVisibleEdges() {
        if (!this.edgeMesh) return;

        const toMerge: THREE.BufferGeometry[] = [];
        this.perRangeEdgeGeoms.forEach((geom, id) => {
            if (!this.hiddenRanges.has(id)) toMerge.push(geom);
        });

        const merged = mergeGeometries(toMerge, false)!;
        this.edgeMesh.geometry.dispose();
        this.edgeMesh.geometry = merged;
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
