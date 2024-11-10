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
import {buildTreeFromUserData} from '../../utils/tree_view/generateTree';
import {handleClickMesh} from "../../utils/mesh_select/handleClickMesh";
import {CustomBatchedMesh} from '../../utils/mesh_select/CustomBatchedMesh';

const ThreeModel: React.FC<ModelProps> = ({url}) => {
    const {raycaster, camera} = useThree();
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
    const {action, setCurrentKey, setSelectedAnimation} = useAnimationStore();
    const {setTranslation, setBoundingBox} = useModelStore();
    const {setTreeData, clearTreeData} = useTreeViewStore();
    const {showEdges, lockTranslation} = useOptionsStore();

    useAnimationEffects(animations, scene);

    useEffect(() => {
        if (scene) {
            useModelStore.getState().setScene(scene);
        }

        raycaster.params.Line.threshold = 0.01;
        raycaster.params.Points.threshold = 0.01;

        // Set raycaster to only detect objects on layer 0
        raycaster.layers.set(0);
        raycaster.layers.disable(1);

        camera.layers.enable(0);
        camera.layers.enable(1);

        const meshesToReplace: { original: THREE.Mesh; parent: THREE.Object3D }[] = [];

        scene.traverse((object) => {
            if (object instanceof THREE.Mesh) {
                meshesToReplace.push({original: object, parent: object.parent!});
            } else if (object instanceof THREE.LineSegments) {
                object.layers.set(1);
            } else if (object instanceof THREE.Points) {
                object.layers.set(1);
            }
        });

        // Replace meshes with CustomBatchedMesh instances
        for (const {original, parent} of meshesToReplace) {
            // Extract draw ranges from userData for the given mesh name
            const meshName = original.name;
            const drawRangesData = scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;

            // Convert drawRangesData to a Map
            const drawRanges = new Map<string, [number, number]>();
            if (drawRangesData) {
                for (const [rangeId, [start, count]] of Object.entries(drawRangesData)) {
                    drawRanges.set(rangeId, [start, count]);
                }
            }

            const customMesh = new CustomBatchedMesh(
                original.geometry,
                original.material,
                drawRanges
            );

            // Copy over properties from original mesh to customMesh
            customMesh.position.copy(original.position);
            customMesh.rotation.copy(original.rotation);
            customMesh.scale.copy(original.scale);
            customMesh.name = original.name;
            customMesh.userData = original.userData;
            customMesh.castShadow = original.castShadow;
            customMesh.receiveShadow = original.receiveShadow;
            customMesh.visible = original.visible;
            customMesh.frustumCulled = original.frustumCulled;
            customMesh.renderOrder = original.renderOrder;
            customMesh.layers.mask = original.layers.mask;

            // Set materials to double-sided and enable flat shading
            if (Array.isArray(customMesh.material)) {
                customMesh.material.forEach((mat) => {
                    if (mat instanceof THREE.MeshStandardMaterial) {
                        mat.side = THREE.DoubleSide;
                        mat.flatShading = true;
                        mat.needsUpdate = true;
                    } else {
                        console.warn('Material is not an instance of MeshStandardMaterial');
                    }
                });
            } else {
                if (customMesh.material instanceof THREE.MeshStandardMaterial) {
                    customMesh.material.side = THREE.DoubleSide;
                    customMesh.material.flatShading = true;
                    customMesh.material.needsUpdate = true;
                } else {
                    console.warn('Material is not an instance of MeshStandardMaterial');
                }
            }

            if (showEdges) {
                // Create edges geometry and add it as a line segment
                const edges = new THREE.EdgesGeometry(customMesh.geometry);
                const lineMaterial = new THREE.LineBasicMaterial({color: 0x000000});
                const edgeLine = new THREE.LineSegments(edges, lineMaterial);

                // Ensure the edge line inherits transformations
                edgeLine.position.copy(customMesh.position);
                edgeLine.rotation.copy(customMesh.rotation);
                edgeLine.scale.copy(customMesh.scale);
                edgeLine.layers.set(1);

                // Add edge lines to the scene
                scene.add(edgeLine);
            }

            // Replace the original mesh with the custom mesh
            parent.add(customMesh);
            parent.remove(original);
        }

        // Replace black materials with default gray material
        replaceBlackMaterials(scene);

        // Compute the bounding box of the model
        const boundingBox = new THREE.Box3().setFromObject(scene);
            setBoundingBox(boundingBox); // Store bounding box in model store
        if (!lockTranslation) {
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
        }

        setSelectedAnimation('No Animation');

        // Generate the tree data and update the store
        const treeData = buildTreeFromUserData(scene);
        if (treeData) setTreeData(treeData);

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
            onPointerDown={handleClickMesh}
            dispose={null}
        />
    );
};

export default ThreeModel;
