import * as THREE from "three";
import {useModelState} from "../../state/modelState";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useOptionsStore} from "../../state/optionsStore";
import {showSelectedPoint} from "../scene/highlightSelectedPoint";

import {gpuPointPicker} from "./GpuPointPicker";
import {useSelectedObjectStore} from "../../state/useSelectedObjectStore";

export async function handleClickPoints(
    intersect: THREE.Intersection,
    event: MouseEvent
): Promise<void> {
    if (event.button === 2) return;

    // Clear the selection if the draw range is already selected
    const selectedObjectStore = useSelectedObjectStore.getState();
    selectedObjectStore.clearSelectedObjects();
    const useGpu = useOptionsStore.getState().useGpuPointPicking;

    let obj = intersect.object as THREE.Points;
    let idx: number | null = (typeof intersect.index === 'number') ? intersect.index : null;
    let worldPosition = intersect.point.clone();

    if (useGpu) {
        const pick = gpuPointPicker.pickAt(event.clientX, event.clientY);
        if (pick) {
            obj = pick.object;
            idx = pick.index;
            worldPosition = pick.worldPosition.clone();
        }
    } else {
        // CPU fallback using raycaster data
        if (idx != null && obj.geometry && (obj.geometry as THREE.BufferGeometry).getAttribute) {
            const geom = obj.geometry as THREE.BufferGeometry;
            const posAttr = geom.getAttribute('position') as THREE.BufferAttribute | undefined;
            if (posAttr && idx >= 0 && idx < posAttr.count) {
                const local = new THREE.Vector3(
                    posAttr.getX(idx),
                    posAttr.getY(idx),
                    posAttr.getZ(idx)
                );
                worldPosition = local.applyMatrix4(obj.matrixWorld);
            }
        }
    }

    // adjust for any translation (model offset)
    const translation = useModelState.getState().translation;
    const clickPosition = worldPosition.clone();
    if (translation) clickPosition.sub(translation);
    useObjectInfoStore.getState().setClickCoordinate(clickPosition);

    // Set a readable name for info box
    const baseName = obj.name || "points";
    const name = idx != null ? `${baseName}[${idx}]` : baseName;
    useObjectInfoStore.getState().setName(name);

    // Show/update highlight for the selected point (use current point size), using exact world position
    const ps = useOptionsStore.getState().pointSize;
    showSelectedPoint(worldPosition.clone(), ps);
}
