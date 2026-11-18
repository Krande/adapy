// Raycasting a `CustomBatchedMesh`: per geometry group, so a range drawn with the invisible
// material (hidden) is never hit, and against the morph-deformed triangles, so a pick lands on
// the deformed shape the user sees.

import * as THREE from "three";

import { applyMorphTargets } from "./morph";

// Scratch objects, reused across calls (raycasting is synchronous).
const _sphere = new THREE.Sphere();
const _inverseMatrix = new THREE.Matrix4();
const _ray = new THREE.Ray();
const _svA = new THREE.Vector3();
const _svB = new THREE.Vector3();
const _svC = new THREE.Vector3();
const _suvA = new THREE.Vector2();
const _suvB = new THREE.Vector2();
const _suvC = new THREE.Vector2();
const _intersectionPoint = new THREE.Vector3();
const _barycoord = new THREE.Vector3();

/**
 * Overrides the raycast method to ignore hidden draw ranges.
 */
export function raycastBatchedMesh(mesh: THREE.Mesh, raycaster: THREE.Raycaster, intersects: THREE.Intersection[]): void {
    const material = mesh.material;

    if (material === undefined) return;

    // Compute the bounding sphere if necessary
    if (mesh.geometry.boundingSphere === null) mesh.geometry.computeBoundingSphere();

    // Check bounding sphere distance to ray
    const sphere = _sphere.copy(mesh.geometry.boundingSphere!);
    sphere.applyMatrix4(mesh.matrixWorld);

    if (raycaster.ray.intersectsSphere(sphere) === false) return;

    // Transform the ray into the local space of the mesh
    const inverseMatrix = _inverseMatrix.copy(mesh.matrixWorld).invert();

    const localRay = _ray.copy(raycaster.ray).applyMatrix4(inverseMatrix);

    // Determine if we have an array of materials
    const isMultiMaterial = Array.isArray(material);

    const materials = isMultiMaterial ? (material as THREE.Material[]) : [material as THREE.Material];

    // Loop over the geometry's groups
    const groups = mesh.geometry.groups;

    for (let i = 0; i < groups.length; i++) {
        const group = groups[i];
        const groupMaterial = group.materialIndex !== undefined ? materials[group.materialIndex] : undefined;

        if (groupMaterial === undefined) continue;

        if (groupMaterial.visible === false) continue;

        // Perform raycasting on this group
        raycastGroup(mesh, localRay, raycaster, group, groupMaterial, intersects);
    }
}

/**
 * Performs raycasting on a specific group of the geometry.
 */
function raycastGroup(
    mesh: THREE.Mesh,
    localRay: THREE.Ray,
    raycaster: THREE.Raycaster,
    group: { start: number; count: number; materialIndex?: number },
    material: THREE.Material,
    intersects: THREE.Intersection[]
): void {
    const geometry = mesh.geometry as THREE.BufferGeometry;
    const index = geometry.index;
    const position = geometry.attributes.position;
    const morphPositions = (geometry.morphAttributes && geometry.morphAttributes.position) as THREE.BufferAttribute[] | undefined;
    const morphTargetsRelative = geometry.morphTargetsRelative === true;
    const morphInfluences: number[] | undefined = mesh.morphTargetInfluences;

    if (position === undefined) return;

    const start = group.start;
    const end = start + group.count;

    const vA = _svA;
    const vB = _svB;
    const vC = _svC;

    // Morph each vertex as the GPU does (`./morph`).
    const applyMorph = (idx: number, target: THREE.Vector3) =>
        applyMorphTargets(target, idx, morphPositions, morphInfluences, morphTargetsRelative);

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

            intersection = checkBufferGeometryIntersection(
                mesh,
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

            intersection = checkBufferGeometryIntersection(
                mesh,
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
function checkBufferGeometryIntersection(
    object: THREE.Mesh,
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
    const intersectionPoint = _intersectionPoint;

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

    intersectionPoint.applyMatrix4(object.matrixWorld);

    const distance = raycaster.ray.origin.distanceTo(intersectionPoint);

    if (distance < raycaster.near || distance > raycaster.far) return null;

    const uvAttribute = object.geometry.attributes.uv;
    let uv: THREE.Vector2 | undefined;

    if (uvAttribute) {
        // ``geometry.attributes.uv`` is typed as
        // ``BufferAttribute | InterleavedBufferAttribute``; both have
        // identical fromBufferAttribute behaviour at runtime, but
        // Vector2.fromBufferAttribute's signature only declares
        // ``BufferAttribute``. Cast through to satisfy the typing.
        const uvAttr = uvAttribute as THREE.BufferAttribute;
        const uvA = _suvA.fromBufferAttribute(uvAttr, a);
        const uvB = _suvB.fromBufferAttribute(uvAttr, b);
        const uvC = _suvC.fromBufferAttribute(uvAttr, c);

        // Compute the UV coordinates at the intersection point
        uv = uvIntersection(_vA, _vB, _vC, uvA, uvB, uvC, intersectionPoint);
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
function uvIntersection(
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
        _barycoord
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
