import React, {useEffect} from 'react';
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
import {PerspectiveCamera} from "three";

const cameraProps = new PerspectiveCamera(60, 1.0, 0.1, 10000);
const CanvasComponent = () => {
    const {modelUrl} = useModelStore();
    const {showPerf} = useNavBarStore(); // use showPerf and setShowPerf from useNavBarStore

    const {handleMeshSelected, handleMeshEmptySpace} = useMeshHandlers();

    const blenderBackgroundColor = "#393939"; // Approximation of Blender's background color

    const canvasParent = document.getElementById('canvasParent');
    const parentWidth = canvasParent?.clientWidth;
    const parentHeight = canvasParent?.clientHeight;

    useEffect(() => {
        cameraProps.aspect = parentWidth && parentHeight ? parentWidth / parentHeight : window.innerWidth / window.innerHeight;
        cameraProps.position.set(5, 5, 5);
        cameraProps.lookAt(0, 0, 0);
    }, []);


    return (
        <div className={"relative w-full h-full"}>
            <div className={"absolute left-0 top-0 z-10 py-2"}>
                <AnimationControls/>
            </div>
            <div className="absolute right-5 top-80 z-10">
                <ColorLegend/>
            </div>


            <div id={"canvasParent"} className={"absolute w-full h-full"}>
                <Canvas
                    camera={cameraProps}
                    onPointerMissed={handleMeshEmptySpace}
                    style={{backgroundColor: blenderBackgroundColor}}>
                    <ambientLight intensity={Math.PI / 2}/>
                    <spotLight position={[50, 50, 50]} angle={0.15} penumbra={1} decay={0} intensity={Math.PI}/>
                    <pointLight position={[-10, -10, -10]} decay={0} intensity={Math.PI}/>
                    {modelUrl && <Model url={modelUrl} onMeshSelected={handleMeshSelected}/>}
                    <GridHelper size={10} divisions={10} colorCenterLine="white" colorGrid="white"/>
                    {showPerf && <Perf/>}
                    <OrbitControls camera={cameraProps} enableDamping={false} makeDefault={false}/>
                    <OrientationGizmo/>
                </Canvas>
            </div>
        </div>
    );
}

export default CanvasComponent;