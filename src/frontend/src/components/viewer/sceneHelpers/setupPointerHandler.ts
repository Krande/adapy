// state/utils/setupPointerHandler.ts
import * as THREE from "three";
import {handleClickEmptySpace} from "@/utils/mesh_select/handleClickEmptySpace";
import {handleClickMesh} from "@/utils/mesh_select/handleClickMesh";
import {handleClickPoints} from "@/utils/mesh_select/handleClickPoints";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
// handleClickMesh signature change accommodates a prefilledRangeId
// from the GPU mesh picker; the raycast fallback still passes only
// the intersection.
import {useOptionsStore} from "@/state/optionsStore";
import {gpuPointPicker} from "@/utils/mesh_select/GpuPointPicker";
import {gpuMeshPicker} from "@/utils/mesh_select/GpuMeshPicker";
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

        // 1b) GPU mesh picking — O(1) regardless of triangle count and
        //     morph-aware for free. Replaces the linear CPU raycast over
        //     every triangle in the scene. On a 3M-tri mobile FEA model
        //     this drops selection latency from ~800ms to ~3ms. Falls
        //     through to the raycast block on miss so plain (non
        //     CustomBatchedMesh) meshes + empty-space clicks still work
        //     via the legacy path.
        try {
            const meshPick = gpuMeshPicker.pickAt(e.clientX, e.clientY);
            if (meshPick) {
                // GPU picking identifies the object + a representative vertex (the draw range's
                // first vertex), NOT the exact clicked point — so "Clicked at" would show the same
                // coordinate for every click on one object (very noticeable on a small model). Refine
                // it with a raycast against just the picked mesh; falls back to the vertex when the
                // mesh is too large to raycast cheaply (keeps the huge-FEA fast path).
                const refined = raycastPointOnMesh(e, camera, renderer, meshPick.mesh);
                const fakeIntersection: THREE.Intersection = {
                    object: meshPick.mesh,
                    point: refined ?? meshPick.worldPosition.clone(),
                    distance: 0,
                } as THREE.Intersection;
                await handleClickMesh(fakeIntersection, e, meshPick.rangeId);
                return;
            }
        } catch (err) {
            console.warn("GPU mesh picking threw; falling back to raycast:", err);
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
            // Only imported geometry (CustomBatchedMesh) is selectable. If the first opaque
            // hit is anything else — a section-plane cap, a helper, a non-shape mesh — treat
            // the click as a deselect, not a no-op: clicking off a shape clears the selection.
            if (nearestMesh.object instanceof CustomBatchedMesh) {
                await handleClickMesh(nearestMesh, e);
            } else {
                handleClickEmptySpace(e);
            }
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

/** World-space surface point under the cursor, for the double-tap
 *  "set rotation pivot" gesture. Returns null on a clean miss.
 *
 *  GPU mesh pick first: depth-buffer accurate and the same picker the
 *  selection path trusts. The CPU raycast fallback must NOT use the
 *  Raycaster defaults — ``Points.threshold`` / ``Line.threshold``
 *  default to 1 *world unit*, so on FEM models (node points + edge
 *  lines everywhere) the "nearest" hit was often a vertex passing
 *  within a unit of the ray right next to the camera. The pivot then
 *  landed essentially at the camera position and rotation degenerated
 *  into spinning in place — easiest to trigger on mobile, where you
 *  pinch in close before double-tapping. */
// Raycast the clicked pixel against a SINGLE mesh to recover the exact world-space hit point.
// Used to refine the GPU picker's representative-vertex position into the real clicked point.
// Bounded by triangle count so a whole-model batched mesh (millions of tris) doesn't stall the
// click — above the cap the caller keeps the fast approximate vertex.
const MAX_REFINE_TRIS = 600_000;

function raycastPointOnMesh(
    e: MouseEvent,
    camera: THREE.PerspectiveCamera,
    renderer: THREE.WebGLRenderer,
    mesh: THREE.Object3D,
): THREE.Vector3 | null {
    try {
        const geom = (mesh as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
        if (!geom) return null;
        const triCount = (geom.index ? geom.index.count : geom.attributes.position?.count ?? 0) / 3;
        if (triCount > MAX_REFINE_TRIS) return null;
        const rect = renderer.domElement.getBoundingClientRect();
        const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        const ny = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        const ray = new THREE.Raycaster();
        ray.layers.set(0);
        ray.layers.disable(1);
        ray.setFromCamera(new THREE.Vector2(nx, ny), camera);
        const hit = ray.intersectObject(mesh, false)[0];
        return hit?.point ? hit.point.clone() : null;
    } catch {
        return null;
    }
}

function pickWorldPoint(
    e: MouseEvent,
    camera: THREE.PerspectiveCamera,
    scene: THREE.Scene,
    renderer: THREE.WebGLRenderer,
): THREE.Vector3 | null {
    try {
        const meshPick = gpuMeshPicker.pickAt(e.clientX, e.clientY);
        if (meshPick) return meshPick.worldPosition.clone();
    } catch {
        // GPU pick unavailable — CPU raycast below still works.
    }
    const rect = renderer.domElement.getBoundingClientRect();
    const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -((e.clientY - rect.top) / rect.height) * 2 + 1;
    const pointer = new THREE.Vector2(nx, ny);
    const ray = new THREE.Raycaster();
    ray.params.Points = {...ray.params.Points, threshold: 0.02};
    ray.params.Line = {...ray.params.Line, threshold: 0.02};
    ray.layers.set(0);
    ray.layers.disable(1);
    ray.setFromCamera(pointer, camera);
    // Never pivot onto something hugging the near plane — a hit that
    // close is a threshold artifact, not a tapped surface.
    const minDist = camera.near * 2;
    const hits = ray.intersectObjects(scene.children, true);
    const hit = hits.find((h) => h.distance > minDist);
    return hit ? hit.point.clone() : null;
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
