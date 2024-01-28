import {useCallback} from 'react';
import {MeshInfo} from "../state/modelInterfaces";
import {useSelectedObjectStore} from "../state/selectedObjectStore";
import * as THREE from "three";
import useWebSocket from './useWebSocket';
import {handleWebSocketMessage} from "../utils/handleWebSocketMessage";
import {useModelStore} from '../state/modelStore';

export const useMeshHandlers = () => {
    const {setModelUrl} = useModelStore();
    const {selectedObject, setSelectedObject, originalColor} = useSelectedObjectStore();
    const sendData = useWebSocket('ws://localhost:8765', handleWebSocketMessage(setModelUrl));

    const handleMeshSelected = useCallback((meshInfo: MeshInfo) => {
        console.log('Mesh clicked:', meshInfo);
        sendData(JSON.stringify({action: 'meshClick', data: meshInfo}));
    }, [sendData]);

    const handleMeshEmptySpace = useCallback((event: MouseEvent) => {
        event.stopPropagation();
        console.log('click on empty space');
        if (selectedObject) {
            console.log(`deselecting object. Reverting to original color ${originalColor}`);
            (selectedObject.material as THREE.MeshBasicMaterial).color.set(originalColor || 'white');
            setSelectedObject(null);
        }
    }, [selectedObject, setSelectedObject, originalColor]);

    return {handleMeshSelected, handleMeshEmptySpace, sendData};
};