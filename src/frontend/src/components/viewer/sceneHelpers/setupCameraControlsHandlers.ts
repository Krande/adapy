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
                drawRangeIds.forEach((drawRangeId) => {
                    mesh.hideDrawRange(drawRangeId);
                });
                mesh.deselect();
            });
            useSelectedObjectStore.getState().clearSelectedObjects();
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
    const box = new THREE.Box3().setFromObject(scene);
    if (box.isEmpty()) return;

    const size = box.getSize(new THREE.Vector3()).length();
    const center = box.getCenter(new THREE.Vector3());
    const distance = size * 0.5;

    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();

    camera.position.copy(center.clone().add(direction.clone().multiplyScalar(-distance)));
    camera.lookAt(center);
    if (controls instanceof OrbitControls) {
        controls.target.copy(center);
    } else if (controls instanceof CameraControls) {
        controls.setLookAt(
            camera.position.x, camera.position.y, camera.position.z,
            center.x, center.y, center.z,
            true // enable smooth transition
        );
    }

    camera.updateProjectionMatrix();
    if (controls instanceof OrbitControls) {
        controls.update();
    }
};
