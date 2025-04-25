// sceneHelpers/addDynamicGridHelper.ts
import * as THREE from "three";

export function addDynamicGridHelper(scene: THREE.Scene): void {
  const grid = new THREE.GridHelper(100, 100, 0x888888, 0x444444);
  (grid.material as THREE.Material).depthWrite = false;
  grid.renderOrder = -1;
  grid.layers.set(1);
  scene.add(grid);
}
