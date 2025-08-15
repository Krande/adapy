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


    if (controls && camera && selectedObjects.size > 0) {
        const boundingBox = new THREE.Box3();
        const vertex = new THREE.Vector3();

        selectedObjects.forEach((drawRangeIds, mesh) => {
            const geometry = mesh.geometry as THREE.BufferGeometry;
            const positionAttr = geometry.getAttribute('position');
            const indexAttr = geometry.getIndex();

            if (!positionAttr) {
                console.warn('Geometry has no position attribute.');
                return;
            }

            if (!indexAttr) {
                console.warn('Geometry has no index buffer.');
                return;
            }

            const indexArray = indexAttr.array as Uint16Array | Uint32Array;
            drawRangeIds.forEach((drawRangeId) => {
                const drawRange = mesh.drawRanges.get(drawRangeId);
                if (drawRange) {
                    const [start, count] = drawRange;

                    if (start < 0 || start + count > indexArray.length) {
                        console.warn(`Draw range (start: ${start}, count: ${count}) is out of bounds.`);
                        return;
                    }

                    for (let i = start; i < start + count; i++) {
                        const index = indexArray[i];
                        if (index < 0 || index >= positionAttr.count) {
                            console.warn(`Index ${index} at position ${i} is out of bounds.`);
                            continue;
                        }

                        vertex.fromBufferAttribute(positionAttr, index);

                        if (isNaN(vertex.x) || isNaN(vertex.y) || isNaN(vertex.z)) {
                            console.warn(`NaN detected in vertex position at index ${index}. Skipping.`);
                            continue;
                        }

                        mesh.localToWorld(vertex);
                        boundingBox.expandByPoint(vertex);
                    }
                } else {
                    console.warn(`Draw range ID ${drawRangeId} not found in mesh.`);
                }
            });
        });

        center_on_bounding_box(boundingBox, camera, fillFactor, controls);

    } else if (controls && selectedPointRef.current) {
        // Use the new zoom-to-point function instead of bounding box
        // zoomToSelectedPoint(selectedPointRef.current, camera, controls);
        // This controls when a point is selected
        const selectedPoint = selectedPointRef.current;
        const position = selectedPoint.position;
        const distance = 1.5;

        const bounding_box = new THREE.Box3()
        // create a box that contains the selected point.
        bounding_box.setFromCenterAndSize(position, new THREE.Vector3(distance, distance, distance));
        // move the camera to the center of the box
        center_on_bounding_box(bounding_box, camera, fillFactor, controls);
    } else {
        console.warn('No selected objects to center view on.');
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
