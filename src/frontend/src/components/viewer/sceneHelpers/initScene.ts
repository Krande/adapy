// sceneHelpers/initScene.ts
import * as THREE from "three";
import { addDynamicGridHelper } from "./addDynamicGridHelper";
import { addOrientationGizmo } from "./addOrientationGizmo";

export function initScene(scene: THREE.Scene, camera: THREE.Camera): void {
  addDynamicGridHelper(scene);
}
