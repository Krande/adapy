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
import {convert_to_custom_batch_mesh} from "../../utils/scene/convert_to_custom_batch_mesh";

const ThreeModel: React.FC<ModelProps> = ({url}) => {
    const {raycaster, camera, scene: canvasScene} = useThree();
    const gltf = useGLTF(url, false) as unknown as GLTFResult;
    const modelScene = gltf.scene;
    const {scene, animations} = useGLTF(url, false) as unknown as GLTFResult;
    const {action, setCurrentKey, setSelectedAnimation} = useAnimationStore();
    const {setTranslation, setBoundingBox, setScene} = useModelStore();
    const {setTreeData, clearTreeData} = useTreeViewStore();
    const {showEdges, lockTranslation} = useOptionsStore();

    useAnimationEffects(animations, scene);

    useEffect(() => {
        console.log("updating model");
        //setScene(canvasScene)
        // Add your glTF model to the canvas scene
        canvasScene.add(modelScene);

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

            const customMesh = convert_to_custom_batch_mesh(original, drawRanges);

            if (showEdges) {
                let edgeLine = customMesh.get_edge_lines();
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
            canvasScene.remove(modelScene); // cleanup
            clearTreeData();
        };
    }, [canvasScene, modelScene]);

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
