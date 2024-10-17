// CanvasComponent.tsx
import React, {useRef} from 'react';
import {Canvas} from '@react-three/fiber';
import {OrbitControls} from '@react-three/drei';
import {OrbitControls as OrbitControlsImpl} from 'three-stdlib';
import ThreeModel from './ThreeModel';
import ColorLegend from './ColorLegend';
import {Perf} from 'r3f-perf';
import OrientationGizmo from './OrientationGizmo';
import {useModelStore} from '../../state/modelStore';
import {useOptionsStore} from '../../state/optionsStore';
import CameraControls from './CameraControls';
import CameraLight from "./CameraLights";
import DynamicGridHelper from './DynamicGridHelper';
import {handleClickEmptySpace} from "../../utils/mesh_select/handleClickEmptySpace";

const CanvasComponent: React.FC = () => {
    const {modelUrl, scene_action, scene_action_arg} = useModelStore();
    const {showPerf} = useOptionsStore();

    const orbitControlsRef = useRef<OrbitControlsImpl>(null);

    const cameraProps = {
        fov: 60,
        near: 0.1,
        far: 10000,
        position: [5, 5, 5] as [number, number, number],
    };

    return (
        <div className="relative w-full h-full">
            <div className="absolute right-5 top-80 z-10">
                <ColorLegend/>
            </div>

            <div id="canvasParent" className="absolute w-full h-full">
                <Canvas
                    shadows={true}
                    camera={cameraProps}
                    onPointerMissed={handleClickEmptySpace}
                    style={{backgroundColor: '#393939'}}
                >
                    {/* Add the CameraLight component */}
                    <CameraLight/>

                    {modelUrl && (
                        <ThreeModel
                            url={modelUrl}
                            scene_action={scene_action}
                            scene_action_arg={scene_action_arg}
                        />
                    )}
                    {/* Replace GridHelper with DynamicGridHelper */}
                    <DynamicGridHelper/>

                    {showPerf && <Perf/>}
                    <OrbitControls ref={orbitControlsRef} enableDamping={false} makeDefault={false}/>
                    <OrientationGizmo/>

                    {/* Render CameraControls inside the Canvas */}
                    <CameraControls orbitControlsRef={orbitControlsRef}/>

                    {/* Todo: Integrate 3Dconnexion Controls */}
                    {/*<ThreeConnexionControls orbitControlsRef={orbitControlsRef} />*/}
                </Canvas>
            </div>
        </div>
    );
};

export default CanvasComponent;
