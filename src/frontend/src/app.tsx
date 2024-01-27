// src/App.js
import "./app.css";

import {Canvas} from '@react-three/fiber'
import React, {useCallback, useState} from 'react'
import {OrbitControls} from '@react-three/drei';
import GridHelper from './components/GridHelper';
import OrientationGizmo from "./components/OrientationGizmo";
import useWebSocket from './hooks/useWebSocket';
import Model from "./components/ThreeModel";
import {Perf} from 'r3f-perf'
import {useModelStore} from './state/modelStore';
import {MeshInfo} from "./state/modelInterfaces";
import {handleWebSocketMessage} from "./utils/handleWebSocketMessage";
import AnimationControls from './components/AnimationControls';
import {useSelectedObjectStore} from "./state/selectedObjectStore";
import * as THREE from "three";
import ColorLegend from "./components/ColorLegend";

function App() {
    const {modelUrl, setModelUrl} = useModelStore();
    const [showPerf, setShowPerf] = useState(true);
    const {selectedObject, setSelectedObject, originalColor} = useSelectedObjectStore();

    const sendData = useWebSocket('ws://localhost:8765', handleWebSocketMessage(setModelUrl));
    const handleMeshSelected = useCallback((meshInfo: MeshInfo) => {
        console.log('Mesh clicked:', meshInfo);
        sendData(JSON.stringify({action: 'meshClick', data: meshInfo}));
    }, [sendData]);

    // Wrapper function for onPlay
    const handleMeshEmptySpace = useCallback((event: MouseEvent) => {
        event.stopPropagation();
        console.log('click on empty space');
        if (selectedObject) {
            console.log(`deselecting object. Reverting to original color ${originalColor}`);
            (selectedObject.material as THREE.MeshBasicMaterial).color.set(originalColor || 'white');
            setSelectedObject(null);
        }

    }, [selectedObject, setSelectedObject, originalColor]);

    const blenderBackgroundColor = "#393939"; // Approximation of Blender's background color

    // Custom camera settings
    const cameraProps = {
        fov: 60, // Adjust this value as needed, a lower value reduces fish-eye effect
        position: [5, 5, 5]
    };

    return (
        <div className={"flex flex-col h-full"}>
            <div className={"absolute left-0 z-10 flex flex-col p-2 space-y-4"}>
                <button
                    className={"flex-1 bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                    onClick={() => sendData('Hello from React')}>Send Message
                </button>
                <button
                    className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                    onClick={() => setShowPerf(!showPerf)}
                >Hide stats
                </button>

                <div className={"flex-1 w-60"}>
                    <AnimationControls/>
                </div>
            </div>

            <Canvas className={"flex-1"}
                // @ts-ignore
                    camera={cameraProps}
                    onPointerMissed={handleMeshEmptySpace}
                    style={{backgroundColor: blenderBackgroundColor}}>
                <ambientLight intensity={Math.PI / 2}/>
                <spotLight position={[50, 50, 50]} angle={0.15} penumbra={1} decay={0} intensity={Math.PI}/>
                <pointLight position={[-10, -10, -10]} decay={0} intensity={Math.PI}/>
                {modelUrl && <Model url={modelUrl} onMeshSelected={handleMeshSelected}/>}
                <GridHelper size={10} divisions={10} colorCenterLine="white" colorGrid="white"/>
                {showPerf && <Perf/>}
                <OrbitControls enableDamping={false}/>
                <OrientationGizmo/>
            </Canvas>

            <div className={"absolute right-5 top-60 h-96 w-28"}>
                <ColorLegend/>
            </div>

        </div>


    );
}

export default App;