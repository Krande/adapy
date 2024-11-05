// CameraControls.tsx
import {useEffect, useRef} from 'react';
import {useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {OrbitControls as OrbitControlsImpl} from 'three-stdlib';
import React from 'react';
import {useModelStore} from "../../state/modelStore";
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {centerViewOnObject} from "../../utils/scene/centerViewOnObject";

type CameraControlsProps = {
    orbitControlsRef: React.RefObject<OrbitControlsImpl>;
};

const CameraControls: React.FC<CameraControlsProps> = ({orbitControlsRef}) => {
    const {camera, gl, scene} = useThree();
    const selectedObjectRef = useRef<THREE.Object3D | null>(null);

    useEffect(() => {
        const handlePointerDown = (event: PointerEvent) => {
            if (event.ctrlKey && event.button === 0) {
               console.log('CTRL+Left Click');
            }
        };

        gl.domElement.addEventListener('pointerdown', handlePointerDown);

        const handleKeyDown = (event: KeyboardEvent) => {
            let currently_selected = useSelectedObjectStore.getState().selectedObject;

            if (event.key.toLowerCase() === 'f' && event.shiftKey) {
                if (currently_selected) {
                    centerViewOnObject(currently_selected, orbitControlsRef, camera);
                }
            } else if (event.key.toLowerCase() === 'h' && event.shiftKey) {
                // Perform an action when "ctrl+h" is pressed
                console.log('SHIFT+H pressed');
                // currently_selected?.layers.set(1);
                // Example action: Reset the camera position to the default
            } else if (event.key.toLowerCase() === 'g' && event.shiftKey) {
                // Perform an action when "ctrl+h" is pressed
                console.log('SHIFT+g pressed');
                // loop over objects in layers 2 and set them to layer 0
                // Example action: Reset the camera position to the default
            }
        };

        window.addEventListener('keydown', handleKeyDown);

        return () => {
            gl.domElement.removeEventListener('pointerdown', handlePointerDown);
            window.removeEventListener('keydown', handleKeyDown);
        };
    }, [camera, gl, scene]);

    return null; // This component doesn't render anything
};

export default CameraControls;
