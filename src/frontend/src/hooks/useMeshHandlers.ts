import {useCallback} from 'react';
import {MeshInfo} from "../state/modelInterfaces";
import {useSelectedObjectStore} from "../state/selectedObjectStore";
import * as THREE from "three";
import {useWebSocketStore} from '../state/webSocketStore';

export const useMeshHandlers = () => {
    const {selectedObject, setSelectedObject, originalColor} = useSelectedObjectStore();

    const handleMeshSelected = useCallback((meshInfo: MeshInfo) => {
        console.log('Mesh clicked:', meshInfo);
    }, []);

    const handleMeshEmptySpace = useCallback((event: MouseEvent) => {
        event.stopPropagation();
        console.log('click on empty space');
        if (selectedObject) {
            console.log(`deselecting object. Reverting to original color ${originalColor}`);
            (selectedObject.material as THREE.MeshBasicMaterial).color.set(originalColor || 'white');
            setSelectedObject(null);
        }
    }, [selectedObject, setSelectedObject, originalColor]);

    return {handleMeshSelected, handleMeshEmptySpace};
};