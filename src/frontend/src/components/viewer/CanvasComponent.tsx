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
import AnimationControls from './AnimationControls';
import ObjectInfoBox from '../object_info_box/ObjectInfoBoxComponent';
import {useObjectInfoStore} from '../../state/objectInfoStore';
import CameraControls from './CameraControls';
import {handleMeshEmptySpace} from '../../utils/mesh_handling';
import CameraLight from "./CameraLights";
import DynamicGridHelper from './DynamicGridHelper';
import use3DConnexion from '../../hooks/use3DConnexion';
import ThreeConnexionControls from "./ThreeConnexionControls";

const CanvasComponent: React.FC = () => {
    const {modelUrl, scene_action, scene_action_arg} = useModelStore();
    const {showPerf} = useOptionsStore();
    const {show_info_box} = useObjectInfoStore();

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
                    onPointerMissed={handleMeshEmptySpace}
                    style={{backgroundColor: '#393939'}}
                >
                    {/* Existing lights can be removed or kept based on your preference */}
                    {/* Remove existing lights if they interfere with the new lighting */}
                    {/*<ambientLight intensity={Math.PI / 2} />*/}
                    {/*<pointLight position={[-10, -10, -10]} decay={0} intensity={Math.PI} />*/}

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
                              {/* Integrate 3Dconnexion Controls */}
                    {/*<ThreeConnexionControls orbitControlsRef={orbitControlsRef} />*/}
                </Canvas>
            </div>
        </div>
    );
};

export default CanvasComponent;
