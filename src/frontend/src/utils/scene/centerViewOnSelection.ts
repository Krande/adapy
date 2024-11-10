// centerViewOnSelection.ts
import * as THREE from 'three';
import {Camera} from 'three';
import React from 'react';
import {OrbitControls as OrbitControlsImpl} from 'three-stdlib/controls/OrbitControls';
import { useSelectedObjectStore } from '../../state/useSelectedObjectStore';

export const centerViewOnSelection = (
    orbitControlsRef: React.RefObject<OrbitControlsImpl>,
    camera: Camera,
    fillFactor: number = 1 // Default to filling the entire view
) => {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;

    if (orbitControlsRef.current && camera && selectedObjects.size > 0) {
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

                    // Validate that start and count are within the index array bounds
                    if (start < 0 || start + count > indexArray.length) {
                        console.warn(
                            `Draw range (start: ${start}, count: ${count}) is out of bounds of the index array (length: ${indexArray.length}).`
                        );
                        return;
                    }

                    for (let i = start; i < start + count; i++) {
                        const index = indexArray[i];

                        // Validate the index
                        if (index < 0 || index >= positionAttr.count) {
                            console.warn(
                                `Index ${index} at position ${i} is out of bounds of the position attribute (count: ${positionAttr.count}). Skipping.`
                            );
                            continue;
                        }

                        // Extract the vertex position at the specified index
                        vertex.fromBufferAttribute(positionAttr, index);

                        // Check for NaN values in the vertex position
                        if (isNaN(vertex.x) || isNaN(vertex.y) || isNaN(vertex.z)) {
                            console.warn(
                                `NaN detected in vertex position at index ${index}. Skipping this vertex.`
                            );
                            continue;
                        }

                        // Apply the object's world matrix to get the world position
                        mesh.localToWorld(vertex);
                        boundingBox.expandByPoint(vertex);
                    }
                } else {
                    console.warn(`Draw range ID ${drawRangeId} not found in mesh.`);
                }
            });
        });

        // Check if the bounding box is valid
        if (!boundingBox.isEmpty()) {
            // Compute bounding sphere from bounding box
            const boundingSphere = new THREE.Sphere();
            boundingBox.getBoundingSphere(boundingSphere);

            // Get the center of the bounding sphere
            const center = boundingSphere.center;
            const radius = boundingSphere.radius;

            if (radius === 0 || isNaN(radius)) {
                console.warn('Selection has zero size or invalid radius.');
                return;
            }

            let distance = 0;
            if (camera instanceof THREE.PerspectiveCamera) {
                const fov = (camera.fov * Math.PI) / 180; // Convert FOV to radians
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
                console.warn('Unknown camera type');
                return;
            }

            // Calculate the new camera position
            const direction = new THREE.Vector3();
            camera.getWorldDirection(direction);
            const newPosition = center.clone().sub(direction.multiplyScalar(distance));
            camera.position.copy(newPosition);

            // Ensure the camera's up vector is correct
            camera.up.set(0, 1, 0); // Assuming Y-up coordinate system

            // Make the camera look at the center
            camera.lookAt(center);

            // Update the camera's projection matrix
            camera.updateProjectionMatrix();

            // Update the orbit controls
            orbitControlsRef.current.target.copy(center);
            orbitControlsRef.current.update();
        } else {
            console.warn(
                'Bounding box is empty after processing selected vertices. Cannot center view.'
            );
        }
    } else {
        console.warn('No selected objects to center view on.');
    }
};
