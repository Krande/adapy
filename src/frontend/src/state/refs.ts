// state/refs.ts
import CameraControls from "camera-controls";
import {createRef} from "react";
import * as THREE from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";

export const cameraRef = createRef<THREE.PerspectiveCamera | null>();
export const controlsRef = createRef<CameraControls | OrbitControls | null>();
export const rendererRef = createRef<THREE.WebGLRenderer | null>();
