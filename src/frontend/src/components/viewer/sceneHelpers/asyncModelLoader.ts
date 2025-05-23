// state/sceneHelpers/asyncModelLoader.ts
import { GLTF, GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader";

// returns a Promise that resolves with the loaded GLTF
export function loadGLTF(modelUrl: string): Promise<GLTF> {
  const loader = new GLTFLoader();
  return new Promise((resolve, reject) => {
    loader.load(modelUrl, resolve, undefined, reject);
  });
}
