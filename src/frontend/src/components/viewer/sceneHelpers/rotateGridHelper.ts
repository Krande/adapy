import * as THREE from "three";

/**
 * Rotates the grid helper in the scene to match the camera's up direction.
 * @param scene - The Three.js scene containing the grid helper.
 * @param zIsUp - A boolean indicating if Z is the up direction.
 */
export function rotateGridHelper(scene: THREE.Scene, zIsUp: boolean) {
    const grid = scene.children.find(
        (child) => child instanceof THREE.GridHelper
    ) as THREE.GridHelper | undefined;

    if (grid) {
        grid.rotation.set(zIsUp ? Math.PI / 2 : 0, 0, 0);
    }
}
