// state/utils/setupPointerHandler.ts
import * as THREE from "three";
import {handleClickEmptySpace} from "../../../utils/mesh_select/handleClickEmptySpace";
import {handleClickMesh} from "../../../utils/mesh_select/handleClickMesh";
import {handleClickPoints} from "../../../utils/mesh_select/handleClickPoints";
import {useOptionsStore} from "../../../state/optionsStore";
import {gpuPointPicker} from "../../../utils/mesh_select/GpuPointPicker";
import {clearSelectedPoint} from "../../../utils/scene/highlightSelectedPoint";

export function setupPointerHandler(
    container: HTMLDivElement,
    camera: THREE.PerspectiveCamera,
    scene: THREE.Scene,
    renderer: THREE.WebGLRenderer,
) {
    let pointerDownPos: { x: number; y: number } | null = null;
    const clickThreshold = 5;

    container.addEventListener("pointerdown", (e) => {
        pointerDownPos = {x: e.clientX, y: e.clientY};
    });

    container.addEventListener("click", async (e: MouseEvent) => {
        if (!pointerDownPos) return;
        const dx = e.clientX - pointerDownPos.x;
        const dy = e.clientY - pointerDownPos.y;
        if (dx * dx + dy * dy > clickThreshold * clickThreshold) return;

        // cast a ray
        const rect = renderer.domElement.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        const pointer = new THREE.Vector2(x, y);
        const ray = new THREE.Raycaster();
        // Set point picking tolerance to 0.005 (world units) per user preference/point spacing.
        ray.params.Points = { ...ray.params.Points, threshold: 0.01 };
        ray.layers.set(0);
        ray.layers.disable(1);
        ray.setFromCamera(pointer, camera);

        const hits = ray.intersectObjects(scene.children, true);

        if (hits.length === 0) {
            // Fallback: if GPU point picking is enabled, try picking deformed/translated points
            if (useOptionsStore.getState().useGpuPointPicking) {
                const pick = gpuPointPicker.pickAt(e.clientX, e.clientY);
                if (pick) {
                    const fakeIntersection: THREE.Intersection = {
                        object: pick.object,
                        index: pick.index,
                        point: pick.worldPosition.clone(),
                        distance: 0,
                    } as THREE.Intersection;
                    await handleClickPoints(fakeIntersection, e);
                    return;
                }
            }
            handleClickEmptySpace(e);
        } else {
            const first = hits[0];
            if (first.object instanceof THREE.Points) {
                await handleClickPoints(first, e);
            } else {
                // await the async handler
                clearSelectedPoint()
                await handleClickMesh(first, e);
            }
        }
    });

    return () => {
        container.removeEventListener("pointerdown", () => {
        });
        container.removeEventListener("click", () => {
        });
    };
}
