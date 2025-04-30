// CustomBatchedMesh.ts
import * as THREE from 'three';
import {selectedMaterial} from '../default_materials';
// utils/mesh_select/CustomBatchedMesh.ts
import {buildEdgeGeometryWithRangeIds, makeEdgeShaderMaterial} from './EdgeShaderHelper';


export class CustomBatchedMesh extends THREE.Mesh {
    originalGeometry: THREE.BufferGeometry;
    originalMaterial: THREE.Material;
    drawRanges: Map<string, [number, number]>;

    private selectedRanges = new Set<string>();
    private hiddenRanges = new Set<string>();

    private edgeMesh?: THREE.LineSegments;
    private rangeIdToIndex?: Map<string, number>;
    private edgeMaterial?: THREE.ShaderMaterial;

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
        super(geometry.clone(), material.clone());
        this.originalGeometry = geometry;
        this.originalMaterial = material.clone();
        this.drawRanges = drawRanges;
        this.updateGroups();
        // no renderer yet → defer edge setup
    }

    private updateGroups() {
        const idxCount = this.geometry.index!.count;
        this.geometry.clearGroups();
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
            else if (this.selectedRanges.has(id)) mi = 1;
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

            this.edgeMaterial = makeEdgeShaderMaterial(renderer, rangeIdToIndex.size);
            this.edgeMesh = new THREE.LineSegments(geometry, this.edgeMaterial);
            this.edgeMesh.layers.set(1);
        }
        return this.edgeMesh;
    }

    public updateSelectionGroups(rangeIds: string[]) {
        this.selectedRanges = new Set(rangeIds);
        this.updateGroups();
        if (this.edgeMaterial && this.rangeIdToIndex) {
            this.edgeMaterial.uniforms.uHighlighted.value =
                rangeIds.length ? this.rangeIdToIndex.get(rangeIds[0])! : -1;
        }
    }

    public clearSelectionGroups() {
        this.updateSelectionGroups([]);
    }

    public hideDrawRange(rangeId: string) {
        this.hiddenRanges.add(rangeId);
        this.updateGroups();
        if (this.edgeMaterial && this.rangeIdToIndex) {
            const i = this.rangeIdToIndex.get(rangeId)!;
            const tex = this.edgeMaterial.uniforms.uVisibleTex.value as THREE.DataTexture;
            tex.image.data[i] = 0;
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
