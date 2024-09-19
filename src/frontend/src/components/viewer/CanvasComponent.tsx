// CanvasComponent.tsx
import React, {useRef} from 'react';
import {Canvas} from '@react-three/fiber';
import {OrbitControls} from '@react-three/drei';
import {OrbitControls as OrbitControlsImpl} from 'three-stdlib';
import GridHelper from './GridHelper';
import ThreeModel from './ThreeModel';
import ColorLegend from './ColorLegend';
import {Perf} from 'r3f-perf';
import OrientationGizmo from './OrientationGizmo';
import {useModelStore} from '../../state/modelStore';
import {useNavBarStore} from '../../state/navBarStore';
import AnimationControls from './AnimationControls';
import ObjectInfoBox from './objectInfo';
import {useObjectInfoStore} from '../../state/objectInfoStore';
import CameraControls from './CameraControls';
import {handleMeshEmptySpace} from '../../utils/mesh_handling';
import CameraLight from "./CameraLights";
import DynamicGridHelper from './DynamicGridHelper'; // Import the new component
const CanvasComponent: React.FC = () => {
    const {modelUrl, scene_action, scene_action_arg} = useModelStore();
    const {showPerf} = useNavBarStore();
    const {show} = useObjectInfoStore();

    const orbitControlsRef = useRef<OrbitControlsImpl>(null);

    const cameraProps = {
        fov: 60,
        near: 0.1,
        far: 10000,
        position: [5, 5, 5] as [number, number, number],
    };

    return (
        <div className="relative w-full h-full">
            <div className="absolute left-0 top-0 z-10 py-2 flex flex-col">
                <AnimationControls/>
                {show && <ObjectInfoBox/>}
            </div>
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
                </Canvas>
            </div>
        </div>
    );
};

export default CanvasComponent;
