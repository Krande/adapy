// CameraControls.tsx
import React, { useEffect } from 'react';
import * as THREE from 'three';
import { useThree } from '@react-three/fiber';
import { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { useSelectedObjectStore } from '../../../state/useSelectedObjectStore';
import { centerViewOnSelection } from '../../../utils/scene/centerViewOnSelection';
import { CustomBatchedMesh } from '../../../utils/mesh_select/CustomBatchedMesh';

type CameraControlsProps = {
    orbitControlsRef: React.RefObject<OrbitControlsImpl>;
};

const CameraControls: React.FC<CameraControlsProps> = ({ orbitControlsRef }) => {
    const { camera, scene } = useThree();

    const zoomToAll = () => {
        // Compute the bounding box of the entire scene
        const box = new THREE.Box3().setFromObject(scene);
        const size = box.getSize(new THREE.Vector3()).length();
        const center = box.getCenter(new THREE.Vector3());

        // Adjust camera position and look direction
        const scale = 0.5;
        camera.position.set(center.x+ size * scale, center.y+ size * scale, center.z + size * scale);
        camera.lookAt(center);

        if (orbitControlsRef.current) {
            orbitControlsRef.current.target.copy(center); // Update the controls' target
            orbitControlsRef.current.update();
        }
    };

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.shiftKey && event.key.toLowerCase() === 'h') {
                // SHIFT+H pressed - Hide selected draw ranges
                const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
                selectedObjects.forEach((drawRangeIds, mesh) => {
                    drawRangeIds.forEach((drawRangeId) => {
                        mesh.hideDrawRange(drawRangeId);
                    });
                    mesh.deselect();
                });
                useSelectedObjectStore.getState().clearSelectedObjects();
            } else if (event.shiftKey && event.key.toLowerCase() === 'u') {
                // SHIFT+U pressed - Unhide all
                scene.traverse((object) => {
                    if (object instanceof CustomBatchedMesh) {
                        object.unhideAllDrawRanges();
                    }
                });
            } else if (event.shiftKey && event.key.toLowerCase() === 'f') {
                // SHIFT+F pressed - Center view on selection
                centerViewOnSelection(orbitControlsRef, camera);
            }
            else if (event.shiftKey && event.key.toLowerCase() === 'a') {
                // SHIFT+a pressed - Zoom to all
                zoomToAll();
            }
        };

        window.addEventListener('keydown', handleKeyDown);

        return () => {
            window.removeEventListener('keydown', handleKeyDown);
        };
    }, [camera, orbitControlsRef, scene]);

    return null;
};

export default CameraControls;
