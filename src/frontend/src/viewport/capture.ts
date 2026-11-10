// Capturing the viewport as an image: screenshots and animation frames. three.js re-renders the scene
// into its own WebGL buffer; with the adacpp WebGPU renderer on screen that image is not the one the
// user sees (different shading, culling, line widths), so the capture can be taken from adacpp instead
// (perfStore `adacppCapture`). Callers ask here which one to use, like picking asks viewport/picking.ts.
import * as THREE from "three";
import {adacppHandover} from "@/state/perfStore";
import type {AdacppViewport} from "./adacpp/adacppViewport";

let adacpp: AdacppViewport | null = null;

/** The adacpp viewport on screen; null when three.js draws. */
export function setCaptureViewport(viewport: AdacppViewport | null): void {
    adacpp = viewport;
}

/** The adacpp viewport to capture from: it is drawing and the capture switch is on. Else null: use three.js. */
export function captureViewport(): AdacppViewport | null {
    return adacpp && adacpp.alive && adacppHandover().capture ? adacpp : null;
}

/** A copy of the view camera with another aspect ratio, so the live camera is untouched. */
export function cameraForAspect(camera: THREE.Camera, aspect: number): THREE.Camera {
    const cam = camera.clone();
    if (cam instanceof THREE.PerspectiveCamera) {
        cam.aspect = aspect;
        cam.updateProjectionMatrix();
    } else if (cam instanceof THREE.OrthographicCamera) {
        // Keep the vertical extent, widen / narrow the horizontal one around its centre.
        const cx = (cam.left + cam.right) / 2;
        const halfH = (cam.top - cam.bottom) / 2;
        cam.left = cx - halfH * aspect;
        cam.right = cx + halfH * aspect;
        cam.updateProjectionMatrix();
    }
    return cam;
}
