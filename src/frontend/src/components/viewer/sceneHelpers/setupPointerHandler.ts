// state/utils/setupPointerHandler.ts
import * as THREE from "three";
import {handleClickEmptySpace} from "@/utils/mesh_select/handleClickEmptySpace";
import {handleClickMesh} from "@/utils/mesh_select/handleClickMesh";
import {handleClickPoints} from "@/utils/mesh_select/handleClickPoints";
import {useOptionsStore} from "@/state/optionsStore";
import {gpuPointPicker} from "@/utils/mesh_select/GpuPointPicker";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import CameraControls from "camera-controls";

// A second click/tap inside this window + radius counts as a double.
// 350ms matches the OS-level double-tap threshold on iOS / Android and
// is well within typical mouse double-click intervals; the 24px radius
// is generous on touch (finger taps are imprecise) and inert for mouse
// (real mouse double-clicks land within a few pixels).
const DOUBLE_TAP_MS = 350;
const DOUBLE_TAP_PX = 24;

export function setupPointerHandler(
    container: HTMLDivElement,
    camera: THREE.PerspectiveCamera,
    scene: THREE.Scene,
    renderer: THREE.WebGLRenderer,
    controls?: CameraControls | OrbitControls,
) {
    let pointerDownPos: { x: number; y: number } | null = null;
    const clickThreshold = 5;

    let lastTapTime = 0;
    let lastTapPos: { x: number; y: number } | null = null;

    container.addEventListener("pointerdown", (e) => {
        pointerDownPos = {x: e.clientX, y: e.clientY};
    });

    container.addEventListener("click", async (e: MouseEvent) => {
        if (!pointerDownPos) return;
        const dx = e.clientX - pointerDownPos.x;
        const dy = e.clientY - pointerDownPos.y;
        if (dx * dx + dy * dy > clickThreshold * clickThreshold) return;

        // Double-click / double-tap → set the camera's rotation pivot
        // to the world point under the cursor. Same gesture for mouse
        // and touch: the first click already set selection state; the
        // second one is dedicated to repositioning the orbit center.
        //
        // We check timing against the previous tap here rather than
        // relying on the browser's `dblclick` event, which fires
        // inconsistently on mobile browsers (Safari iOS in particular)
        // and would arrive AFTER the second `click` had already
        // re-toggled selection on desktop.
        const now = performance.now();
        if (
            controls &&
            lastTapPos &&
            now - lastTapTime <= DOUBLE_TAP_MS &&
            Math.hypot(e.clientX - lastTapPos.x, e.clientY - lastTapPos.y) <= DOUBLE_TAP_PX
        ) {
            // Reset so a third quick tap doesn't immediately pair with
            // the second and chain double-taps.
            lastTapTime = 0;
            lastTapPos = null;
            const point = pickWorldPoint(e, camera, scene, renderer);
            if (point) {
                applyOrbitPoint(controls, point);
                // Skip normal selection on the double-tap — the first
                // tap already set the selection state; the second is
                // dedicated to camera repositioning.
                return;
            }
            // No surface under the second tap → fall through to normal
            // selection logic. Avoids "double-tap on empty space did
            // nothing" surprise.
        }
        lastTapTime = now;
        lastTapPos = {x: e.clientX, y: e.clientY};

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

/** Raycast at screen coords and return the nearest world-space hit on
 *  any mesh (points hits also count). Returns null on a clean miss. */
function pickWorldPoint(
    e: MouseEvent,
    camera: THREE.PerspectiveCamera,
    scene: THREE.Scene,
    renderer: THREE.WebGLRenderer,
): THREE.Vector3 | null {
    const rect = renderer.domElement.getBoundingClientRect();
    const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    const pointer = new THREE.Vector2(nx, ny);
    const ray = new THREE.Raycaster();
    ray.layers.set(0);
    ray.layers.disable(1);
    ray.setFromCamera(pointer, camera);
    const hits = ray.intersectObjects(scene.children, true);
    return hits.length > 0 ? hits[0].point.clone() : null;
}

/** Set the camera's rotation pivot without moving the camera. */
function applyOrbitPoint(
    controls: CameraControls | OrbitControls,
    point: THREE.Vector3,
): void {
    if (controls instanceof CameraControls) {
        // setOrbitPoint pivots without translating the camera, which
        // is what the user expects from a "set rotation center" gesture.
        controls.setOrbitPoint(point.x, point.y, point.z);
    } else {
        // OrbitControls' .target *is* the rotation pivot. Updating it
        // re-anchors orbit/zoom around the new point.
        controls.target.copy(point);
        controls.update();
    }
}
