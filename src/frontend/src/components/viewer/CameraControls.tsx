// CameraControls.tsx
import React, { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { useSelectedObjectStore } from '../../state/useSelectedObjectStore';
import { centerViewOnSelection } from '../../utils/scene/centerViewOnSelection';
import { CustomBatchedMesh } from '../../utils/mesh_select/CustomBatchedMesh';

type CameraControlsProps = {
    orbitControlsRef: React.RefObject<OrbitControlsImpl>;
};

const CameraControls: React.FC<CameraControlsProps> = ({ orbitControlsRef }) => {
    const { camera, scene } = useThree();

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
        };

        window.addEventListener('keydown', handleKeyDown);

        return () => {
            window.removeEventListener('keydown', handleKeyDown);
        };
    }, [camera, orbitControlsRef, scene]);

    return null;
};

export default CameraControls;
