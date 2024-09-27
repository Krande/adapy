// CameraControls.tsx
import {useEffect, useRef} from 'react';
import {useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {OrbitControls as OrbitControlsImpl} from 'three-stdlib';
import React from 'react';

type CameraControlsProps = {
    orbitControlsRef: React.RefObject<OrbitControlsImpl>;
};

const CameraControls: React.FC<CameraControlsProps> = ({orbitControlsRef}) => {
    const {camera, gl, scene} = useThree();
    const selectedObjectRef = useRef<THREE.Object3D | null>(null);

    const centerViewOnObject = (object: THREE.Object3D) => {
        if (orbitControlsRef.current && camera) {
            const controls = orbitControlsRef.current;
            const boundingBox = new THREE.Box3().setFromObject(object);
            const center = boundingBox.getCenter(new THREE.Vector3());

            // Update controls target
            controls.target.copy(center);

            // Calculate appropriate camera position
            const size = boundingBox.getSize(new THREE.Vector3());
            const maxDim = Math.max(size.x, size.y, size.z);

            if (camera instanceof THREE.PerspectiveCamera) {
                const fov = (camera.fov * Math.PI) / 180;
                const cameraDistance = maxDim / Math.tan(fov / 2);

                camera.position.copy(
                    center.clone().add(new THREE.Vector3(cameraDistance, cameraDistance, cameraDistance))
                );

                camera.updateProjectionMatrix();
                controls.update();
            } else {
                console.warn('Camera is not a PerspectiveCamera');
            }
        }
    };

    useEffect(() => {
        const handlePointerDown = (event: PointerEvent) => {
            if (event.ctrlKey && event.button === 0) {
                const rect = gl.domElement.getBoundingClientRect();
                const mouse = new THREE.Vector2(
                    ((event.clientX - rect.left) / rect.width) * 2 - 1,
                    -((event.clientY - rect.top) / rect.height) * 2 + 1
                );

                const raycaster = new THREE.Raycaster();
                raycaster.setFromCamera(mouse, camera);

                const intersects = raycaster.intersectObjects(scene.children, true);

                if (intersects.length > 0) {
                    const selectedObject = intersects[0].object;
                    selectedObjectRef.current = selectedObject;
                    centerViewOnObject(selectedObject);
                }
            }
        };

        gl.domElement.addEventListener('pointerdown', handlePointerDown);

        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key.toLowerCase() === 'f' && !event.ctrlKey && !event.altKey && !event.metaKey) {
                if (selectedObjectRef.current) {
                    centerViewOnObject(selectedObjectRef.current);
                }
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
