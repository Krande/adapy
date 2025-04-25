// src/components/ThreeConnexionControls.tsx

import React, { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import use3DConnexion from '../../../hooks/use3DConnexion';
import { OrbitControls } from 'three-stdlib';

interface ThreeConnexionControlsProps {
  orbitControlsRef: React.RefObject<OrbitControls>;
}

const ThreeConnexionControls: React.FC<ThreeConnexionControlsProps> = ({ orbitControlsRef }) => {
  const { camera, scene, gl } = useThree();

  // Define the GLInstance object based on the sample code
  const glInstance = {
    camera,
    scene,
    renderer: gl,
    loadModel: () => { /* Implement model loading if necessary */ },
    // Add other properties and methods as needed
  };

  const controller = use3DConnexion(glInstance, orbitControlsRef);

  useEffect(() => {
    if (controller) {
      // Implement any additional setup if necessary

      // Example: Handle custom commands
      controller.setActiveCommand = (id: string) => {
        console.log(`Command activated: ${id}`);
        // Implement command actions based on the ID
      };
    }
  }, [controller]);

  return null; // This component does not render anything
};

export default ThreeConnexionControls;
