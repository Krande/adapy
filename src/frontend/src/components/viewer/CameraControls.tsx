// CameraControls.tsx
import * as THREE from 'three';
import React, {useEffect} from 'react';
import {useThree} from '@react-three/fiber';
import {OrbitControls as OrbitControlsImpl} from 'three-stdlib';
import {useSelectedObjectStore} from "../../state/selectedObjectStore";
import {centerViewOnObject} from "../../utils/scene/centerViewOnObject";

type CameraControlsProps = {
    orbitControlsRef: React.RefObject<OrbitControlsImpl>;
};

const CameraControls: React.FC<CameraControlsProps> = ({orbitControlsRef}) => {
    const {camera, gl, scene} = useThree();

    useEffect(() => {
        const handlePointerDown = (event: PointerEvent) => {
            if (event.ctrlKey && event.button === 0) {
               console.log('CTRL+Left Click');
            }
        };

        gl.domElement.addEventListener('pointerdown', handlePointerDown);

        const handleKeyDown = (event: KeyboardEvent) => {

            if (event.key.toLowerCase() === 'f' && event.shiftKey) {
                centerViewOnObject(orbitControlsRef, camera);
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
