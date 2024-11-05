import * as THREE from "three";
import React from "react";
import {OrbitControls as OrbitControlsImpl} from "three-stdlib/controls/OrbitControls";
import {Camera} from "three";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {getSelectedMeshDrawRange} from "../mesh_select/getSelectedMeshDrawRange";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {useModelStore} from "../../state/modelStore";

export const centerViewOnObject = (
    object: THREE.Object3D,
    orbitControlsRef: React.RefObject<OrbitControlsImpl>,
    camera: Camera
) => {
    let current_object = useSelectedObjectStore.getState().selectedObject;
    let face_index = useObjectInfoStore.getState().faceIndex;
    let translation = useModelStore.getState().translation;

    if (orbitControlsRef.current && camera && current_object && face_index) {
        let draw_range = getSelectedMeshDrawRange(object as THREE.Mesh, face_index);
        if (draw_range) {
            const [rangeId, start, count] = draw_range;

            // Ensure the object has a BufferGeometry
            const geometry = (object as THREE.Mesh).geometry as THREE.BufferGeometry;

            // Check that the geometry has a position attribute
            if (geometry && geometry.hasAttribute("position")) {
                const position = geometry.getAttribute("position");
                // if position is NA, return
                if (!position) {
                    return;
                }
                // Create a bounding box based on the specified draw range
                const boundingBox = new THREE.Box3();
                const boundingBoxLocal = new THREE.Box3();
                const vertex = new THREE.Vector3();

                for (let i = start; i < start + count; i++) {
                    // Extract the vertex position at the specified index
                    vertex.fromBufferAttribute(position, i);
                    // if any of the vertex positions are NA, return
                    if (vertex.x === null || vertex.y === null || vertex.z === null) {
                        return;
                    }
                    // Apply the object's world matrix to get the world position
                    boundingBoxLocal.expandByPoint(vertex);
                    object.localToWorld(vertex);
                    boundingBox.expandByPoint(vertex);
                }

                const scene = useModelStore.getState().scene;
                if (scene) {
                    // Delete the previous bounding box helper
                    scene.children.forEach((child) => {
                        if (child instanceof THREE.Box3Helper) {
                            scene.remove(child);
                        }
                    });
                    // Add a Box3Helper to visualize the bounding box
                    const boundingBoxHelper = new THREE.Box3Helper(boundingBoxLocal, 0xff0000); // Red color for visibility

                    // move the bounding box helper back using the opposite directed translation vector
                    if (translation) {
                        boundingBoxHelper.position.sub(translation);
                    }

                    scene.add(boundingBoxHelper);
                }

                // Get the center of the bounding box
                const center = boundingBox.getCenter(new THREE.Vector3());
                camera.lookAt(center);// Update the orbit controls
                orbitControlsRef.current.target.copy(center);
                orbitControlsRef.current.update();
                // Update the camera's projection matrix
                if (camera instanceof THREE.PerspectiveCamera || camera instanceof THREE.OrthographicCamera) {
                    camera.updateProjectionMatrix();
                }
                // Log the center
                console.log(`Bounding box center: ${center.x}, ${center.y}, ${center.z}`);

                // Get the camera's current viewing direction
                const direction = new THREE.Vector3();
                camera.getWorldDirection(direction);

                // Log the camera direction before moving
                console.log(`Camera direction before: ${direction.x}, ${direction.y}, ${direction.z}`);

                // Desired distance from center (3 meters)
                const distance = 3;

                // Calculate the new camera position
                const new_position = center.clone().sub(direction.clone().multiplyScalar(distance));

                // Log the new camera position
                console.log(`New camera position: ${new_position.x}, ${new_position.y}, ${new_position.z}`);

                // Move the camera to the new position
                camera.position.copy(new_position);

                // Ensure the camera's up vector is correct
                camera.up.set(0, 1, 0); // Assuming Y-up coordinate system

                // Make the camera look at the center
                camera.lookAt(center);

                // Update the camera's projection matrix
                if (camera instanceof THREE.PerspectiveCamera || camera instanceof THREE.OrthographicCamera) {
                    camera.updateProjectionMatrix();
                }

                // Update the camera's direction after moving
                const new_direction = new THREE.Vector3();
                camera.getWorldDirection(new_direction);

                // Log the camera direction after moving
                console.log(`Camera direction after: ${new_direction.x}, ${new_direction.y}, ${new_direction.z}`);

                // Update the orbit controls
                orbitControlsRef.current.target.copy(center);
                orbitControlsRef.current.update();
                console.log(`Bounding box center: ${center.x}, ${center.y}, ${center.z}`);
            }
        }
    }
};
