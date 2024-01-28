// Model.tsx
import React, {useEffect} from 'react';
import {useGLTF} from '@react-three/drei';
import {ThreeEvent, useFrame} from "@react-three/fiber";
import * as THREE from 'three'
import {GLTFResult, ModelProps} from "../../state/modelInterfaces";
import {useAnimationStore} from '../../state/animationStore';
import {useAnimationEffects} from '../../hooks/useAnimationEffects';
import {useSelectedObjectStore} from '../../state/selectedObjectStore';


const Model: React.FC<ModelProps> = ({url, onMeshSelected}) => {
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
    const {selectedObject, setSelectedObject, setOriginalColor, originalColor} = useSelectedObjectStore();
    const {action, setCurrentKey, setSelectedAnimation} = useAnimationStore();

    useAnimationEffects(animations, scene);

    useEffect(() => {
        scene.traverse((object) => {
            if (object instanceof THREE.Mesh) {
                object.material.side = THREE.DoubleSide;
                console.log(object.material)
            }
        });

        setSelectedAnimation('No Animation');

    }, [scene]);

    const handleMeshSelected = (event: ThreeEvent<PointerEvent>) => {
        event.stopPropagation();
        const mesh = event.object as THREE.Mesh;
        if (selectedObject !== mesh) {
            if (selectedObject) {
                (selectedObject.material as THREE.MeshBasicMaterial).color.set(originalColor || 'white');
            }

            const material = mesh.material as THREE.MeshBasicMaterial;
            setOriginalColor(material.color);
            setSelectedObject(mesh);
            const meshInfo = {
                name: mesh.name,
                materialName: material.name,
                intersectionPoint: event.point,
                faceIndex: event.faceIndex || 0,
                meshClicked: true,
            };
            onMeshSelected(meshInfo);
        }
    }

    useFrame((_, delta) => {
        if (action) {
            action.getMixer().update(delta);
            setCurrentKey(action.time);
        }
    });

    return <primitive object={scene}
                      onClick={handleMeshSelected}
                      dispose={null}/>;
};

export default Model;