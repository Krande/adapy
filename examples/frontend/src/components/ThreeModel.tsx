// Model.tsx
import React, {useState} from 'react';
import {useGLTF} from '@react-three/drei';
import {ThreeEvent, useFrame} from "@react-three/fiber";
import * as THREE from 'three'
import {GLTFResult, ModelProps} from "../state/modelInterfaces";
import {useAnimationStore} from '../state/animationStore';
import {useAnimationEffects} from '../hooks/useAnimationEffects';
import {handleClick} from '../utils/handleClick';
import {useSelectedObjectStore} from '../state/selectedObjectStore';

const Model: React.FC<ModelProps> = ({url, onMeshSelected}) => {
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
    const {selectedObject, setSelectedObject} = useSelectedObjectStore();
    const {action, setCurrentKey} = useAnimationStore();

    useAnimationEffects(animations, scene);

    useFrame((_, delta) => {
        if (action) {
            action.getMixer().update(delta);
            setCurrentKey(action.time);
        }
    });

    return <primitive object={scene}
                      onClick={(event: ThreeEvent<PointerEvent>) => handleClick(event, selectedObject, setSelectedObject, onMeshSelected)}
                      dispose={null}/>;
};

export default Model;