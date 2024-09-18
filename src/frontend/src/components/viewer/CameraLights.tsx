// CameraLight.tsx

import React, { useRef, useEffect } from 'react';
import { useThree, useFrame } from '@react-three/fiber';
import * as THREE from 'three';

const CameraLight: React.FC = () => {
  const lightRef = useRef<THREE.DirectionalLight>(null);
  const { camera } = useThree();

  useEffect(() => {
    if (lightRef.current) {
      lightRef.current.castShadow = true; // Enable casting shadows
      lightRef.current.shadow.mapSize.width = 2048; // Increase shadow map size for better quality
      lightRef.current.shadow.mapSize.height = 2048;
      lightRef.current.shadow.camera.near = 0.5;
      lightRef.current.shadow.camera.far = 500;
      lightRef.current.shadow.bias = -0.0001; // Adjust bias to reduce shadow artifacts
    }
  }, []);

  useFrame(() => {
    if (lightRef.current) {
      // Update the light's position to match the camera's position
      lightRef.current.position.copy(camera.position);

      // Calculate the camera's forward direction
      const direction = new THREE.Vector3();
      camera.getWorldDirection(direction);

      // Set the light's target in front of the camera
      lightRef.current.target.position.copy(camera.position.clone().add(direction));
      lightRef.current.target.updateMatrixWorld();
    }
  });

  return (
    <>
      <directionalLight
        ref={lightRef}
        intensity={1}
        color={0xffffff}
        castShadow
      />
    </>
  );
};

export default CameraLight;
