// Model.tsx
import React, { useEffect } from 'react';
import { useGLTF } from '@react-three/drei';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { GLTFResult, ModelProps } from '../../state/modelInterfaces';
import { useAnimationStore } from '../../state/animationStore';
import { useAnimationEffects } from '../../hooks/useAnimationEffects';
import { handleMeshSelected } from '../../utils/mesh_handling';
import { useModelStore } from '../../state/modelStore'; // Import useModelStore
import {replaceBlackMaterials} from '../../utils/assignDefaultMaterial'; // Adjust the import path
import { Edges } from '@react-three/drei';

const Model: React.FC<ModelProps> = ({ url }) => {
  const { raycaster } = useThree();
  const { scene, animations } = useGLTF(url, false) as unknown as GLTFResult;
  const { action, setCurrentKey, setSelectedAnimation } = useAnimationStore();
  const { setTranslation } = useModelStore(); // Get setTranslation from modelStore

  useAnimationEffects(animations, scene);

  useEffect(() => {
    raycaster.params.Line.threshold = 0.01;

    // Ensure materials are double-sided
    scene.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.material.side = THREE.DoubleSide;
      }
      // Enable shadow casting and receiving
      object.castShadow = true;
      object.receiveShadow = true;
    });

    // Assign default gray material to meshes missing materials
    replaceBlackMaterials(scene);

    // Compute the bounding box of the model
    const boundingBox = new THREE.Box3().setFromObject(scene);
    const center = boundingBox.getCenter(new THREE.Vector3());

    // Compute the translation vector to move the model to the origin
    const translation = center.clone().multiplyScalar(-1);

    // Apply the translation to the model
    scene.position.add(translation);

    // Store the translation vector in the model store
    setTranslation(translation);

    setSelectedAnimation('No Animation');
  }, [scene]);

  useFrame((_, delta) => {
    if (action) {
      action.getMixer().update(delta);
      setCurrentKey(action.time);
    }
  });

  // Inside your return statement
return (
  <group>
    <primitive
      object={scene}
      onClick={handleMeshSelected}
      dispose={null}
    />
    {/* Add edges for each mesh */}
    {scene.children.map((child, index) => {
      if (child instanceof THREE.Mesh) {
        return (
          <Edges
            key={index}
            geometry={child.geometry}
            position={child.position}
            scale={child.scale}
            rotation={child.rotation}
            color={0x000000}
            lineWidth={1}
          />
        );
      }
      return null;
    })}
  </group>
);
};

export default Model;
