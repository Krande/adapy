import * as THREE from "three";
import {useModelState} from "@/state/modelState";
import type {OwnedSceneObjects} from "@/utils/scene/ownedSceneObjects";
import {GROUND_GRID} from "@/viewport/api/groundGrid";

export function addDynamicGridHelper(owned: OwnedSceneObjects): THREE.GridHelper {
    const {zIsUp} = useModelState.getState();

    const grid = new THREE.GridHelper(GROUND_GRID.size, GROUND_GRID.divisions, GROUND_GRID.centreColor, GROUND_GRID.lineColor);
    (grid.material as THREE.Material).depthWrite = false;
    grid.renderOrder = -1;
    grid.layers.set(1);

    if (zIsUp) {
        // Rotate grid from XZ (default) to XY
        grid.rotation.x = Math.PI / 2;
    }

    return owned.add(grid);
}
