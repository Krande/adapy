import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import * as THREE from "three";
import {Object3D} from "three";
import {clearPointSelectionMask} from "../scene/pointsImpostor";
import {sceneRef} from "@/state/refs";

export function handleClickEmptySpace(_event: MouseEvent) {
    // Fully deselect: clearSelectedObjects clears each object's visual selection groups AND
    // empties the selectedObjects map, so the info box / tree view also drop the selection.
    // (Clearing only the visual groups, as this used to, left a stale selection in the store.)
    useSelectedObjectStore.getState().clearSelectedObjects();

    // Also drop point-selection masks on any Points NOT tracked in the store.
    const scene = sceneRef.current;
    if (!scene) return;
    scene.traverse((child: Object3D) => {
        if (child instanceof THREE.Points) {
            clearPointSelectionMask(child as THREE.Points);
        }
    });
}