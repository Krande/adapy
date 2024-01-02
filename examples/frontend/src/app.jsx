// src/App.js
import {Canvas} from '@react-three/fiber'
import React, {useCallback, useState} from 'react'
import {OrbitControls, useGLTF} from '@react-three/drei';
import GridHelper from './GridHelper';
import OrientationGizmo from "./OrientationGizmo";
import useWebSocket from './hooks/useWebSocket';
import Model from "./Model";

function App() {
    const [modelUrl, setModelUrl] = useState(null);
    const handleWebSocketMessage = (event) => {
        if (event.data instanceof Blob) {
            console.log('Blob received');
            const blob = new Blob([event.data], {type: 'model/gltf-binary'});
            const url = URL.createObjectURL(blob);
            setModelUrl(url); // Set the URL for the model
        } else {
            console.log('Message from server ', event.data);
        }
    };

    const sendData = useWebSocket('ws://localhost:8765', handleWebSocketMessage);
    const handleMeshSelected = useCallback((meshInfo) => {
        console.log('Mesh clicked:', meshInfo);
        sendData(JSON.stringify({action: 'meshClick', data: meshInfo}));
    }, [sendData]);


    const blenderBackgroundColor = "#393939"; // Approximation of Blender's background color

    // Custom camera settings
    const cameraProps = {
        fov: 60, // Adjust this value as needed, a lower value reduces fish-eye effect
        position: [5, 5, 5]
    };
    const style = {
        width: '100vw', // Use viewport width
        height: '100vh', // Use viewport height
    };
    return (
        <div style={style}>
            <button onClick={() => sendData('Hello from React')}>Send Message</button>
            <Canvas camera={cameraProps} style={{backgroundColor: blenderBackgroundColor}}>
                <ambientLight intensity={Math.PI / 2}/>
                <spotLight position={[10, 10, 10]} angle={0.15} penumbra={1} decay={0} intensity={Math.PI}/>
                <pointLight position={[-10, -10, -10]} decay={0} intensity={Math.PI}/>
                {modelUrl && <Model url={modelUrl} onMeshSelected={handleMeshSelected}/>}
                <GridHelper size={10} divisions={10} colorCenterLine="white" colorGrid="white"/>
                <OrbitControls/>
                <OrientationGizmo/>
            </Canvas>
        </div>
    );
}

export default App;