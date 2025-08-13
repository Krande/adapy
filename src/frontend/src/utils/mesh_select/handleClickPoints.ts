import * as THREE from "three";
import {useModelState} from "../../state/modelState";
import {useObjectInfoStore} from "../../state/objectInfoStore";
import {useOptionsStore} from "../../state/optionsStore";
import {showSelectedPoint} from "../scene/highlightSelectedPoint";

export async function handleClickPoints(
    intersect: THREE.Intersection,
    event: MouseEvent
): Promise<void> {
    if (event.button === 2) return;

    const obj = intersect.object as THREE.Points;
    const idx = typeof intersect.index === 'number' ? intersect.index : null;

    // Determine the precise world-space position of the clicked vertex
    let worldPosition = intersect.point.clone(); // fallback
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
