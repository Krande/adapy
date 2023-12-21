// src/App.js
import React from 'react';
import { Canvas } from '@react-three/fiber';
import { useGLTF, OrbitControls } from '@react-three/drei';

function Model({ url }) {
  const gltf = useGLTF(url);
  return <primitive object={gltf.scene} />;
}

function App() {
  // Replace 'path/to/model.gltf' with the actual path to your GLTF model
  const modelUrl = './boxed_merged.glb';

  return (
    <Canvas>
      <ambientLight intensity={0.5} />
      <spotLight position={[10, 10, 10]} angle={0.15} penumbra={1} />
      <pointLight position={[-10, -10, -10]} />
      <Model url={modelUrl} />
      <OrbitControls />
    </Canvas>
  );
}

export default App;