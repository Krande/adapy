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

        // 1) Try GPU point picking first so points in front of meshes are prioritized
        if (useOptionsStore.getState().useGpuPointPicking) {
            try {
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
                // if pick is null => miss; continue to raycast
            } catch (err) {
                console.warn("GPU picking threw an error; falling back to raycast:", err);
            }
        }

        // 2) Raycast scene as fallback to detect meshes and points
        const rect = renderer.domElement.getBoundingClientRect();
        const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const ny = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        const pointer = new THREE.Vector2(nx, ny);
        const ray = new THREE.Raycaster();

        // Set point picking tolerance (world units) for CPU points fallback
        ray.params.Points = { ...ray.params.Points, threshold: 0.02 };
        ray.layers.set(0);
        ray.layers.disable(1);
        ray.setFromCamera(pointer, camera);

        const hits = ray.intersectObjects(scene.children, true);

        // Separate nearest mesh vs nearest points from raycast results
        let nearestMesh: THREE.Intersection | null = null;
        let nearestPoints: THREE.Intersection | null = null;
        for (const h of hits) {
            if ((h.object as any).isPoints) {
                if (!nearestPoints) nearestPoints = h;
            } else {
                nearestMesh = h;
                break; // meshes are opaque/solid; take the first
            }
        }

        if (nearestMesh) {
            // Clear any highlighted point if not multi-select
            if (!e.shiftKey) clearSelectedPoint();
            await handleClickMesh(nearestMesh, e);
            return;
        }

        // 3) If raycast had a points hit, use it; else treat as empty space
        if (nearestPoints) {
            await handleClickPoints(nearestPoints, e);
        } else {
            handleClickEmptySpace(e);
        }
    });

    return () => {
        container.removeEventListener("pointerdown", () => {
        });
        container.removeEventListener("click", () => {
        });
    };
}
