import * as THREE from "three";
import {useAnimationStore} from "../state/animationStore";
import {useModelStore} from "../state/modelStore";
import {cameraRef, controlsRef} from "../state/refs";

export function debug_print() {
    console.log("Debug print function called");
    let animation_store = useAnimationStore.getState();
    if (!animation_store.hasAnimation) {
        console.log("No animation found");

    } else {
        console.log("Animation found");
    }

    let model_store = useModelStore.getState();
    let scene = model_store.scene;
    // print camera position and target
    if (!scene) {
        console.log("Scene is null");
        return;
    }

    if (!cameraRef.current) {
        console.log("No cameras found in the scene");
    } else {
        let camera = cameraRef.current;
        console.log("Camera position:", camera.position);
        console.log("Camera rotation:", camera.rotation);
        console.log("Camera up:", camera.up);
        console.log("Camera world up:", camera.getWorldDirection(new THREE.Vector3()));
        console.log("Camera world direction:", camera.getWorldDirection(new THREE.Vector3()));
        console.log("Camera quaternion:", camera.quaternion);
        console.log("Camera matrix world:", camera.matrixWorld);
        console.log("Camera matrix world inverse:", camera.matrixWorldInverse);
        console.log("Camera projection matrix:", camera.projectionMatrix);
        console.log("Camera projection matrix inverse:", camera.projectionMatrixInverse);
        console.log("Camera frustum:", camera.projectionMatrix.elements);
        console.log("Camera frustum planes:", camera.projectionMatrix.elements);
        console.log("Camera frustum size:", camera.projectionMatrix.elements.length);
    }

    if (!controlsRef.current) {
        console.log("No controls found in the scene");
    } else {
        let controls = controlsRef.current;
        console.log("Controls target:", controls.target);
        console.log("Controls position:", controls.object.position);
    }

}