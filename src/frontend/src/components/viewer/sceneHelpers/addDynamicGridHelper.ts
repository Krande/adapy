import * as THREE from "three";
import {useModelState} from "../../../state/modelState"; // assuming correct path

export function addDynamicGridHelper(scene: THREE.Scene): THREE.GridHelper {
    const {zIsUp} = useModelState.getState();

    const grid = new THREE.GridHelper(100, 100, 0x888888, 0x444444);
    (grid.material as THREE.Material).depthWrite = false;
    grid.renderOrder = -1;
    grid.layers.set(1);

    if (zIsUp) {
        // Rotate grid from XZ (default) to XY
        grid.rotation.x = Math.PI / 2;
    }

    scene.add(grid);
    return grid;
}
