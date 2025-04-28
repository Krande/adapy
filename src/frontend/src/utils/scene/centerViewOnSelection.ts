import * as THREE from 'three';
import {Camera} from 'three';
import {useSelectedObjectStore} from '../../state/useSelectedObjectStore';
import {useModelStore} from '../../state/modelStore';
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls';
import CameraControls from 'camera-controls';

export const centerViewOnSelection = (
    orbitControlsRef: OrbitControls | CameraControls,
    camera: Camera,
    fillFactor: number = 1 // Default to filling the entire view
) => {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
    const {zIsUp} = useModelStore.getState();

    if (orbitControlsRef && camera && selectedObjects.size > 0) {
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

        if (!boundingBox.isEmpty()) {
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
            if (orbitControlsRef instanceof OrbitControls) {
                orbitControlsRef.target.copy(center);
                orbitControlsRef.update();
            }
        } else {
            console.warn('Bounding box is empty after processing selected vertices.');
        }
    } else {
        console.warn('No selected objects to center view on.');
    }
};
