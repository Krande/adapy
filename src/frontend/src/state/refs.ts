// state/refs.ts
import CameraControls from "camera-controls";
import {createRef} from "react";
import * as THREE from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {AnimationController} from "../utils/scene/animations/AnimationController";
import {SimulationDataExtensionMetadata, ADADesignAndAnalysisExtension} from "../extensions/design_and_analysis_extension";

export const cameraRef = createRef<THREE.PerspectiveCamera | null>();
export const controlsRef = createRef<CameraControls | OrbitControls | null>();
export const rendererRef = createRef<THREE.WebGLRenderer | null>();
export const sceneRef = createRef<THREE.Scene | null>();
export const updatelightRef = createRef<() => void>();
export const animationControllerRef = createRef<AnimationController | null>();
export const simulationDataRef = createRef<SimulationDataExtensionMetadata | null>();
export const adaExtensionRef = createRef<ADADesignAndAnalysisExtension | null>();
export const modelKeyMapRef = createRef<Map<string, THREE.Object3D | THREE.Group> | null>();