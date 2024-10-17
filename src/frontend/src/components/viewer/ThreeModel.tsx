// ThreeModel.tsx
import React, {useEffect} from 'react';
import {useGLTF} from '@react-three/drei';
import {useFrame, useThree} from '@react-three/fiber';
import * as THREE from 'three';
import {GLTFResult, ModelProps} from '../../state/modelInterfaces';
import {useAnimationStore} from '../../state/animationStore';
import {useAnimationEffects} from '../../hooks/useAnimationEffects';
import {useModelStore} from '../../state/modelStore';
import {replaceBlackMaterials} from '../../utils/scene/assignDefaultMaterial';
import {useTreeViewStore} from '../../state/treeViewStore';
import {useOptionsStore} from "../../state/optionsStore";
import { generateTree } from '../../utils/tree_view/generateTree';
import {handleClickMesh} from "../../utils/mesh_select/handleClickMesh";


const ThreeModel: React.FC<ModelProps> = ({url}) => {
    const {raycaster} = useThree();
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
    const {action, setCurrentKey, setSelectedAnimation} = useAnimationStore();
    const {setTranslation, setBoundingBox} = useModelStore();
    const {setTreeData, clearTreeData} = useTreeViewStore();
    const {showEdges} = useOptionsStore();

    useAnimationEffects(animations, scene);

    useEffect(() => {
        raycaster.params.Line.threshold = 0.01;

        scene.traverse((object) => {
            if (object instanceof THREE.Mesh) {
                // Ensure geometry has normals
                if (!object.geometry.hasAttribute('normal')) {
                    object.geometry.computeVertexNormals();
                }

                // Set materials to double-sided and enable flat shading
                if (Array.isArray(object.material)) {
                    object.material.forEach((mat) => {
                        mat.side = THREE.DoubleSide;
                        mat.flatShading = true;
                        mat.needsUpdate = true;
                    });
                } else {
                    object.material.side = THREE.DoubleSide;
                    object.material.flatShading = true;
                    object.material.needsUpdate = true;
                }

                if (showEdges) {
                    // Create edges geometry and add it as a line segment
                    const edges = new THREE.EdgesGeometry(object.geometry);
                    const lineMaterial = new THREE.LineBasicMaterial({color: 0x000000});
                    const edgeLine = new THREE.LineSegments(edges, lineMaterial);

                    // Make sure the edge line inherits position and rotation of the object
                    edgeLine.position.copy(object.position);
                    edgeLine.rotation.copy(object.rotation);
                    edgeLine.scale.copy(object.scale);

                    // Add edge lines to the scene
                    scene.add(edgeLine);
                }

                // Enable shadow casting and receiving
                // object.castShadow = true;
                // object.receiveShadow = true;
            }
        });

        // Replace black materials with default gray material
        replaceBlackMaterials(scene);

        // Compute the bounding box of the model
        const boundingBox = new THREE.Box3().setFromObject(scene);
        setBoundingBox(boundingBox); // Store bounding box in model store

        const center = boundingBox.getCenter(new THREE.Vector3());

        // Compute the translation vector to move the model to the origin
        const translation = center.clone().multiplyScalar(-1);
        const minY = boundingBox.min.y;
        const bheight = boundingBox.max.y - minY;
        translation.y = -minY + bheight * 0.05;
        // Apply the translation to the model
        scene.position.add(translation);

        // Store the translation vector in the model store
        setTranslation(translation);

        setSelectedAnimation('No Animation');
        // Generate the tree data and update the store
        const treeData = generateTree(scene);
        if (treeData)
            setTreeData(treeData); // Update the tree view store with the scene graph data

        // Cleanup when the component is unmounted
        return () => {
            clearTreeData();
        };
    }, [scene]);

    useFrame((_, delta) => {
        if (action) {
            action.getMixer().update(delta);
            setCurrentKey(action.time);
        }
    });

    return (
        <primitive
            object={scene}
            onClick={handleClickMesh}
            dispose={null}
        />
    );
};

export default ThreeModel;
