import React, {useState} from 'react';
import {Canvas} from '@react-three/fiber';
import {OrbitControls} from '@react-three/drei';
import GridHelper from './GridHelper';
import Model from "./ThreeModel";
import ColorLegend from "./ColorLegend";
import {Perf} from 'r3f-perf';
import OrientationGizmo from "./OrientationGizmo";
import {useModelStore} from '../../state/modelStore';
import {useMeshHandlers} from "../../hooks/useMeshHandlers";
import AnimationControls from "./AnimationControls";

const CanvasComponent = () => {
    const {modelUrl} = useModelStore();
    const [showPerf, setShowPerf] = useState(false);

    const {handleMeshSelected, handleMeshEmptySpace} = useMeshHandlers();

    const blenderBackgroundColor = "#393939"; // Approximation of Blender's background color

    const cameraProps = {
        fov: 60, // Adjust this value as needed, a lower value reduces fish-eye effect
        position: [5, 5, 5]
    };

    return (
        <div className={""}>
            <div className={""}>
                <div className={""}>
                    <button
                        className={""}
                        onClick={() => setShowPerf(!showPerf)}
                    >Show stats
                    </button>

                    <div className={""}>
                        <AnimationControls/>
                    </div>
                </div>

            </div>
            <div className="">
                <ColorLegend/>
            </div>


            <div className={""}>
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