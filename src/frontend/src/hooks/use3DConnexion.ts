// src/hooks/use3DConnexion.ts

import { useEffect, useRef } from 'react';
import { Camera, Vector3, Matrix4, Raycaster } from 'three';

interface GLInstance {
  camera: Camera;
  scene: any;
  renderer: any;
  loadModel: () => any;
  // Add other properties and methods as needed
}

const use3DConnexion = (glInstance: GLInstance, orbitControlsRef: React.RefObject<any>) => {
  const controllerRef = useRef<any>(null);

  useEffect(() => {
    if (window._3Dconnexion) {
      const controller = new window._3Dconnexion(glInstance);
      controller.connect();

      controller.onConnect = () => {
        const canvas = document.getElementById('canvasParent') as HTMLElement;
        controller.create3dmouse(canvas, 'React Three Fiber App');
      };

      controller.on3dmouseCreated = () => {
        // Define your action sets and commands here
        const actionTree = new window._3Dconnexion.ActionTree();
        const actionImages = new window._3Dconnexion.ImageCache();

        // Example: Adding a category and actions
        const fileNode = actionTree.push(new window._3Dconnexion.Category('CAT_ID_FILE', 'File'));
        fileNode.push(new window._3Dconnexion.Action('ID_OPEN', 'Open', 'Open file'));
        fileNode.push(new window._3Dconnexion.Action('ID_CLOSE', 'Close', 'Close file'));

        // Load images
        actionImages.push(window._3Dconnexion.ImageItem.fromURL('images/open.png', 'ID_OPEN'));
        actionImages.push(window._3Dconnexion.ImageItem.fromURL('images/close.png', 'ID_CLOSE'));

        // Update the controller with commands
        controller.update3dcontroller({
          commands: { activeSet: 'Default', tree: actionTree },
          images: { images: actionImages.images },
        });
      };

      controllerRef.current = controller;

      // Cleanup on unmount
      return () => {
        // Implement disconnect if necessary
      };
    } else {
      console.warn('3Dconnexion library is not loaded.');
    }
  }, [glInstance]);

  return controllerRef.current;
};

export default use3DConnexion;
