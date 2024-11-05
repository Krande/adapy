import * as THREE from "three";
import React from "react";
import {OrbitControls as OrbitControlsImpl} from "three-stdlib/controls/OrbitControls";
import {Camera} from "three";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {getSelectedMeshDrawRange} from "../mesh_select/getSelectedMeshDrawRange";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {useModelStore} from "../../state/modelStore";


export const centerViewOnObject = (object: THREE.Object3D, orbitControlsRef: React.RefObject<OrbitControlsImpl>, camera: Camera) => {
    let current_object = useSelectedObjectStore.getState().selectedObject
    let face_index = useObjectInfoStore.getState().faceIndex
    if (orbitControlsRef.current && camera && current_object && face_index) {
        let draw_range = getSelectedMeshDrawRange(object as THREE.Mesh, face_index)
        if (draw_range) {

            const [rangeId, start, count] = draw_range;
            // get the bounding box of the selected faces
            // Ensure the object has a BufferGeometry
            const geometry = (object as THREE.Mesh).geometry as THREE.BufferGeometry;

            // Check that the geometry has a position attribute
            if (geometry && geometry.hasAttribute('position')) {
                const position = geometry.getAttribute('position');

                // Create a bounding box based on the specified draw range
                const boundingBox = new THREE.Box3();
                const vertex = new THREE.Vector3();

                for (let i = start; i < start + count; i++) {
                    // Extract the vertex position at the specified index
                    vertex.fromBufferAttribute(position, i);
                    boundingBox.expandByPoint(vertex);
                }

                const scene = useModelStore.getState().scene;
                if (scene) {
                    // delete the previous bounding box helper
                    scene.children.forEach((child) => {
                        if (child instanceof THREE.Box3Helper) {
                            scene.remove(child);
                        }
                    });
                    // Add a Box3Helper to visualize the bounding box
                    const boundingBoxHelper = new THREE.Box3Helper(boundingBox, 0xff0000); // Red color for visibility
                    scene.add(boundingBoxHelper);
                }


                // Center the camera on the bounding box
                const center = boundingBox.getCenter(new THREE.Vector3());
                orbitControlsRef.current.target.copy(center);

                // Calculate direction vector from camera to center
                const direction = new THREE.Vector3().subVectors(center, camera.position).normalize();

                // Calculate the distance needed to fit the bounding box within the view
                const size = boundingBox.getSize(new THREE.Vector3());
                const maxDimension = Math.max(size.x, size.y, size.z);

                // Adjust distance based on the camera type
                let distance;
                if (camera instanceof THREE.PerspectiveCamera) {
                    const fov = THREE.MathUtils.degToRad(camera.fov); // Convert fov to radians
                    distance = maxDimension / (2 * Math.tan(fov / 2));
                } else if (camera instanceof THREE.OrthographicCamera) {
                    // Adjust orthographic camera's zoom to fit the object
                    const aspectRatio = camera.right / camera.top;
                    camera.zoom = Math.min(camera.right / (maxDimension * aspectRatio), camera.top / maxDimension);
                    camera.updateProjectionMatrix();
                    distance = maxDimension; // Set a default distance for positioning
                } else {
                    console.warn('Unsupported camera type');
                    return;
                }

                // Position the camera based on calculated distance
                camera.lookAt(center);
                camera.position.copy(center).addScaledVector(direction.negate(), distance);
                orbitControlsRef.current.update();
            }
        }
        // const controls = orbitControlsRef.current;
        // const boundingBox = new THREE.Box3().setFromObject(object);
        // const center = boundingBox.getCenter(new THREE.Vector3());
        //
        // Update controls target
        // controls.target.copy(center);

        // // Calculate appropriate camera position
        // const size = boundingBox.getSize(new THREE.Vector3());
        // const maxDim = Math.max(size.x, size.y, size.z);
        //
        // if (camera instanceof THREE.PerspectiveCamera) {
        //     const fov = (camera.fov * Math.PI) / 180;
        //     const cameraDistance = maxDim / Math.tan(fov / 2);
        //
        //     camera.position.copy(
        //         center.clone().add(new THREE.Vector3(cameraDistance, cameraDistance, cameraDistance))
        //     );
        //
        //     camera.updateProjectionMatrix();
        //     controls.update();
        // } else {
        //     console.warn('Camera is not a PerspectiveCamera');
        // }
    }
};