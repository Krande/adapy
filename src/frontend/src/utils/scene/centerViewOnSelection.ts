import * as THREE from 'three';
import {Camera} from 'three';
import {useSelectedObjectStore} from '../../state/useSelectedObjectStore';
import {useModelState} from '../../state/modelState';
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls';
import CameraControls from 'camera-controls';
import {selectedPointRef} from "../../state/refs";

export const centerViewOnSelection = (
    controls: OrbitControls | CameraControls,
    camera: Camera,
    fillFactor: number = 1 // Default to filling the entire view
) => {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;


    if (controls && camera) {
        const boundingBox = new THREE.Box3();
        const vertex = new THREE.Vector3();
        let expanded = false;

        if (selectedObjects.size > 0) {
            selectedObjects.forEach((drawRangeIds, obj) => {
                const mesh = obj as any;
                if (!mesh || !mesh.geometry) return;

                const geometry = mesh.geometry as THREE.BufferGeometry;
                const positionAttr = geometry.getAttribute('position') as THREE.BufferAttribute | undefined;
                if (!positionAttr) return;

                // Morph target data if present (handles deformed meshes / morph targets)
                const morphPositions = (geometry.morphAttributes && (geometry.morphAttributes as any).position) as THREE.BufferAttribute[] | undefined;
                const morphTargetsRelative = geometry.morphTargetsRelative === true;
                const influences: number[] | undefined = (mesh as any).morphTargetInfluences;

                const applyMorph = (idx: number, target: THREE.Vector3) => {
                    if (!morphPositions || !influences) return;
                    let sumInfluence = 0;
                    for (let m = 0; m < morphPositions.length; m++) {
                        const inf = influences[m] || 0;
                        if (inf === 0) continue;
                        sumInfluence += inf;
                        const mp = morphPositions[m];
                        const mx = mp.getX(idx);
                        const my = mp.getY(idx);
                        const mz = mp.getZ(idx);
                        if (morphTargetsRelative) {
                            target.x += mx * inf;
                            target.y += my * inf;
                            target.z += mz * inf;
                        } else {
                            target.x = target.x * (1 - sumInfluence) + mx * inf;
                            target.y = target.y * (1 - sumInfluence) + my * inf;
                            target.z = target.z * (1 - sumInfluence) + mz * inf;
                        }
                    }
                };

                // Handle Points objects: use 'sel' attribute to find selected indices
                if ((mesh as any).isPoints) {
                    const selAttr = geometry.getAttribute('sel') as THREE.BufferAttribute | undefined;
                    if (selAttr) {
                        for (let i = 0; i < selAttr.count; i++) {
                            const sel = selAttr.getX(i);
                            if (sel > 0.5) {
                                vertex.fromBufferAttribute(positionAttr, i);
                                applyMorph(i, vertex);
                                if (isNaN(vertex.x) || isNaN(vertex.y) || isNaN(vertex.z)) continue;
                                mesh.localToWorld(vertex);
                                boundingBox.expandByPoint(vertex);
                                expanded = true;
                            }
                        }
                    }
                    center_on_bounding_box(boundingBox, camera, fillFactor, controls);
                    return;
                }

                // Handle triangle meshes with drawRanges as before
                const indexAttr = geometry.getIndex();
                if (!indexAttr || !mesh.drawRanges) {
                    return;
                }
                const indexArray = indexAttr.array as Uint16Array | Uint32Array;

                drawRangeIds.forEach((drawRangeId) => {
                    const drawRange = mesh.drawRanges.get(drawRangeId);
                    if (!drawRange) return;
                    const [start, count] = drawRange;
                    if (start < 0 || start + count > indexArray.length) return;

                    for (let i = start; i < start + count; i++) {
                        const index = indexArray[i];
                        if (index < 0 || index >= positionAttr.count) continue;

                        vertex.fromBufferAttribute(positionAttr, index);
                        applyMorph(index, vertex); // morph-aware
                        if (isNaN(vertex.x) || isNaN(vertex.y) || isNaN(vertex.z)) continue;
                        mesh.localToWorld(vertex);
                        boundingBox.expandByPoint(vertex);
                        expanded = true;
                    }
                });
            });
        }

        if (expanded && !boundingBox.isEmpty()) {
            center_on_bounding_box(boundingBox, camera, fillFactor, controls);
        } else if (selectedPointRef.current) {
            const selectedPoint = selectedPointRef.current;
            const position = selectedPoint.position;
            const distance = 1.5;
            const bounding_box = new THREE.Box3();
            bounding_box.setFromCenterAndSize(position, new THREE.Vector3(distance, distance, distance));
            center_on_bounding_box(bounding_box, camera, fillFactor, controls);
        } else {
            console.warn('No selected geometry or points to center view on.');
        }
    } else {
        console.warn('Controls or camera not available to center view.');
    }
};

function center_on_bounding_box(boundingBox: THREE.Box3, camera: Camera, fillFactor: number = 1, controls: OrbitControls | CameraControls) {
    if (boundingBox.isEmpty()) {
        console.warn('Bounding box is empty after processing selected vertices.');
        return;
    }

    const {zIsUp} = useModelState.getState();
    const boundingSphere = new THREE.Sphere();
    boundingBox.getBoundingSphere(boundingSphere);

    const center = boundingSphere.center;
    const radius = boundingSphere.radius;

    if (radius === 0 || isNaN(radius)) {
        console.warn('Selection has zero size or invalid radius.');
        return;
    }

    let distance = 0;
    if (camera instanceof THREE.PerspectiveCamera) {
        const fov = (camera.fov * Math.PI) / 180; // Radians
        distance = (radius / Math.sin(fov / 2)) / fillFactor;
    } else if (camera instanceof THREE.OrthographicCamera) {
        const aspect = camera.right / camera.top;
        camera.top = radius / fillFactor;
        camera.bottom = -radius / fillFactor;
        camera.left = (-radius * aspect) / fillFactor;
        camera.right = (radius * aspect) / fillFactor;
        camera.updateProjectionMatrix();
        distance = camera.position.distanceTo(center);
    } else {
        console.warn('Unknown camera type.');
        return;
    }

    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();

    // Move opposite the view direction by "distance" units
    const newPosition = center.clone().add(direction.multiplyScalar(-distance));
    camera.position.copy(newPosition);

    // Set correct up vector
    if (zIsUp) {
        camera.up.set(0, 0, 1);
    } else {
        camera.up.set(0, 1, 0);
    }

    camera.lookAt(center);
    camera.updateProjectionMatrix();
    if (controls instanceof OrbitControls) {
        controls.target.copy(center);
        controls.update();
    } else {  // CameraControls
        const cameraControls = controls as CameraControls;
        const sphere = new THREE.Sphere(center, radius / fillFactor);
        void cameraControls.fitToSphere(sphere, true); // true = enable smooth transition
    }
}

/**
 * Smoothly moves the camera toward a selected point and centers the view on it
 * Uses the same robust approach as center_on_bounding_box
 */
function zoomToSelectedPoint(
    selectedPoint: { position: THREE.Vector3 },
    camera: Camera,
    controls: OrbitControls | CameraControls,
    targetDistance: number = 2.0 // Distance from the point to position camera
) {
    const {zIsUp} = useModelState.getState();
    const targetPosition = selectedPoint.position.clone();

    // Use the same robust approach as center_on_bounding_box
    let distance = targetDistance;

    if (camera instanceof THREE.PerspectiveCamera) {
        // For perspective camera, calculate distance based on FOV to maintain consistent apparent size
        const fov = (camera.fov * Math.PI) / 180; // Radians
        const pointRadius = 0.1; // Treat point as a small sphere
        distance = (pointRadius / Math.sin(fov / 2)) * 20; // Scale up to make point visible
        distance = Math.max(distance, targetDistance); // Ensure minimum distance
    } else if (camera instanceof THREE.OrthographicCamera) {
        // For orthographic camera, adjust zoom level
        const pointRadius = 0.1;
        camera.top = pointRadius * 10;
        camera.bottom = -pointRadius * 10;
        camera.left = -pointRadius * 10 * (camera.right / camera.top);
        camera.right = pointRadius * 10 * (camera.right / camera.top);
        camera.updateProjectionMatrix();
        distance = targetDistance;
    }

    // Get current camera direction (same as center_on_bounding_box)
    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();

    // Calculate new camera position by moving opposite to view direction
    const newPosition = targetPosition.clone().add(direction.multiplyScalar(-distance));
    camera.position.copy(newPosition);

    // Set correct up vector
    if (zIsUp) {
        camera.up.set(0, 0, 1);
    } else {
        camera.up.set(0, 1, 0);
    }

    // Make camera look at the target point
    camera.lookAt(targetPosition);

    // Update projection matrix for cameras that have it
    if (camera instanceof THREE.PerspectiveCamera || camera instanceof THREE.OrthographicCamera) {
        camera.updateProjectionMatrix();
    }

    // Update controls based on type
    if (controls instanceof OrbitControls) {
        controls.target.copy(targetPosition);
        controls.update();
    } else if (controls instanceof CameraControls) {
        // For CameraControls, create a small sphere around the point and use fitToSphere
        const cameraControls = controls as CameraControls;
        const pointRadius = distance / 20; // Small radius relative to viewing distance
        const sphere = new THREE.Sphere(targetPosition, pointRadius);
        void cameraControls.fitToSphere(sphere, true); // true = enable smooth transition
    }
}
