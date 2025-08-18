import * as THREE from "three";
import {useSelectedObjectStore} from "../../../state/useSelectedObjectStore";
import {CustomBatchedMesh} from "../../../utils/mesh_select/CustomBatchedMesh";
import {centerViewOnSelection} from "../../../utils/scene/centerViewOnSelection";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {useOptionsStore} from "../../../state/optionsStore";
import CameraControls from "camera-controls";

export function setupCameraControlsHandlers(
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    controls: CameraControls | OrbitControls,
) {


    const handleKeyDown = (event: KeyboardEvent) => {
        const key = event.key.toLowerCase();
        const shift = event.shiftKey;
        const selectedObjects = useSelectedObjectStore.getState().selectedObjects;

        if (shift && key === "h") {
            selectedObjects.forEach((drawRangeIds, mesh) => {
                if (!(mesh instanceof CustomBatchedMesh)) {
                    // loop recursively through the children of the mesh
                    (mesh as THREE.Object3D).traverse((child: THREE.Object3D) => {
                        if (child instanceof CustomBatchedMesh) {
                            child.hideBatchDrawRange(drawRangeIds);
                        }
                    });
                } else {
                    mesh.hideBatchDrawRange(drawRangeIds);
                }
            });
        } else if (shift && key === "u") {
            scene.traverse((obj) => {
                if (obj instanceof CustomBatchedMesh) {
                    obj.unhideAllDrawRanges();
                }
            });
        } else if (shift && key === "f") {
            centerViewOnSelection(controls, camera);
        } else if (shift && key === "a") {
            zoomToAll(scene, camera, controls);
        } else if (shift && key === "q") {
            const {isOptionsVisible, setIsOptionsVisible} = useOptionsStore.getState();
            setIsOptionsVisible(!isOptionsVisible);
        }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
        window.removeEventListener("keydown", handleKeyDown);
    };
}

const zoomToAll = (scene: THREE.Scene, camera: THREE.PerspectiveCamera, controls: OrbitControls | CameraControls) => {
    // Compute bounding box only from imported/visible meshes, excluding helpers like GridHelper
    const overallBox = new THREE.Box3();
    let hasMesh = false;

    scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
            const objBox = new THREE.Box3().setFromObject(obj);
            if (!objBox.isEmpty()) {
                overallBox.union(objBox);
                hasMesh = true;
            }
        }
    });

    if (!hasMesh || overallBox.isEmpty()) return;

    // Compute a bounding sphere from the overall box for robust FOV-based fitting
    const sphere = overallBox.getBoundingSphere(new THREE.Sphere());
    if (!sphere || sphere.radius === 0) return;

    const center = sphere.center.clone();
    const radius = sphere.radius;

    // Compute required distance so the sphere fits both vertically and horizontally
    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const aspect = camera.aspect || 1;
    const vDist = radius / Math.tan(vFov / 2);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
    const hDist = radius / Math.tan(hFov / 2);
    const distance = Math.max(vDist, hDist);

    // Move the camera back along its current viewing direction
    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();
    const newPosition = center.clone().add(direction.clone().multiplyScalar(-distance));

    if (controls instanceof OrbitControls) {
        camera.position.copy(newPosition);
        camera.lookAt(center);
        controls.target.copy(center);
    } else if (controls instanceof CameraControls) {
        controls.setLookAt(
            newPosition.x, newPosition.y, newPosition.z,
            center.x, center.y, center.z,
            true // enable smooth transition
        );
    }

    camera.updateProjectionMatrix();
    if (controls instanceof OrbitControls) {
        controls.update();
    }
};
