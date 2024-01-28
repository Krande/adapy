import React from 'react';
import {Canvas} from '@react-three/fiber';
import {OrbitControls} from '@react-three/drei';
import GridHelper from './GridHelper';
import Model from "./ThreeModel";
import ColorLegend from "./ColorLegend";
import {Perf} from 'r3f-perf';
import OrientationGizmo from "./OrientationGizmo";
import {useModelStore} from '../../state/modelStore';
import {useNavBarStore} from "../../state/navBarStore";
import {useMeshHandlers} from "../../hooks/useMeshHandlers";
import AnimationControls from "./AnimationControls";
import {useWebSocketStore} from "../../state/webSocketStore";
import useWebSocket from "../../hooks/useWebSocket";
import {handleWebSocketMessage} from "../../utils/handleWebSocketMessage";

const CanvasComponent = () => {
    const {modelUrl} = useModelStore();
    const {showPerf} = useNavBarStore(); // use showPerf and setShowPerf from useNavBarStore
    const {webSocketAddress, setWebSocketAddress, sendData: oldSendData} = useWebSocketStore();
    const {setModelUrl} = useModelStore();

    useWebSocket(webSocketAddress, handleWebSocketMessage(setModelUrl));

    const {handleMeshSelected, handleMeshEmptySpace} = useMeshHandlers();

    const blenderBackgroundColor = "#393939"; // Approximation of Blender's background color

    const cameraProps = {
        fov: 60, // Adjust this value as needed, a lower value reduces fish-eye effect
        position: [5, 5, 5]
    };

    return (
        <div className={"relative w-full h-full"}>
            <div className={"absolute left-0 top-0 z-10 p-2 flex flex-col w-60 space-y-4"}>
                <AnimationControls/>
            </div>
            <div className="absolute right-5 top-80 z-10">
                <ColorLegend/>
            </div>


            <div className={"absolute w-full h-full"}>
                <Canvas
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
            </div>
        </div>
    );
}

export default CanvasComponent;