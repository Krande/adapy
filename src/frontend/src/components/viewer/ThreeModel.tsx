// Model.tsx
import React, {useEffect} from 'react';
import {useGLTF} from '@react-three/drei';
import {useFrame, useThree} from "@react-three/fiber";
import * as THREE from 'three'
import {GLTFResult, ModelProps} from "../../state/modelInterfaces";
import {useAnimationStore} from '../../state/animationStore';
import {useAnimationEffects} from '../../hooks/useAnimationEffects';
import {handleMeshSelected} from "../../utils/mesh_handling";


const Model: React.FC<ModelProps> = ({url, onMeshSelected}) => {
    const {raycaster} = useThree();
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
    const {action, setCurrentKey, setSelectedAnimation} = useAnimationStore();

    useAnimationEffects(animations, scene);

    useEffect(() => {
        raycaster.params.Line.threshold = 0.01;
        scene.traverse((object) => {
            if (object instanceof THREE.Mesh) {
                // Check if the geometry of the mesh is an instance of THREE.BufferGeometry or THREE.Geometry
                if (object.geometry instanceof THREE.BufferGeometry) {
                    // This is a volume-bounding mesh, do nothing
                } else {
                    // This is a flat surface mesh, set the material to DoubleSide
                    object.material.side = THREE.DoubleSide;
                }
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