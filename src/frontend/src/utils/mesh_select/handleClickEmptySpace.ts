import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "./CustomBatchedMesh";
import * as THREE from "three";
import {Object3D} from "three";
import {clearPointSelectionMask} from "../scene/pointsImpostor";
import {sceneRef} from "../../state/refs";

export function handleClickEmptySpace(event: MouseEvent) {
    const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
    selectedObjects.forEach((drawRangeIds, mesh) => {
        if (!(mesh instanceof CustomBatchedMesh)) {
            // loop recursively through the children of the mesh
            (mesh as Object3D).traverse((child: Object3D) => {
                if (child instanceof CustomBatchedMesh) {
                    child.clearSelectionGroups();
                }
            });
        } else if ((mesh as any).isPoints) {
            clearPointSelectionMask(mesh as unknown as THREE.Points);
        } else {
            mesh.clearSelectionGroups();
        }
    });

    // traverse over Three.POINTS in scene objects
    const scene = sceneRef.current;
    if (!scene) {
        return;
    }
    (scene.traverse((child: Object3D) => {
            if (child instanceof THREE.Points) {
                clearPointSelectionMask(child as THREE.Points);
            }
        }
    ))
}