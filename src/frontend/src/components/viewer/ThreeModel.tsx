// Model.tsx
import React, {useEffect} from 'react';
import {useGLTF} from '@react-three/drei';
import {useFrame} from "@react-three/fiber";
import * as THREE from 'three'
import {GLTFResult, ModelProps} from "../../state/modelInterfaces";
import {useAnimationStore} from '../../state/animationStore';
import {useAnimationEffects} from '../../hooks/useAnimationEffects';
import {handleMeshSelected} from "../../utils/mesh_handling";


const Model: React.FC<ModelProps> = ({url, onMeshSelected}) => {
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
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