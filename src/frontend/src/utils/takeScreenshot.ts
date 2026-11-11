import * as THREE from 'three';
import {getViewerRuntime} from "@/state/viewerRuntime";
import {cameraForAspect, captureViewport} from "@/viewport/capture";
import {downloadCanvas} from "@/utils/downloadCanvas";

const SCREENSHOT_WIDTH = 1920;
const SCREENSHOT_HEIGHT = 1080;

const download = (canvas: HTMLCanvasElement): void => downloadCanvas(canvas, 'screenshot.png');

/** The screenshot taken from the adacpp WebGPU image, when that renderer is on screen and captures are
 *  handed over to it. False: three.js takes it. */
function takeAdacppScreenshot(scene: THREE.Scene, camera: THREE.Camera): boolean {
    const viewport = captureViewport();
    if (!viewport) return false;
    const canvas = document.createElement('canvas');
    canvas.width = SCREENSHOT_WIDTH;
    canvas.height = SCREENSHOT_HEIGHT;
    const ctx = canvas.getContext('2d');
    if (!ctx) return false;
    getViewerRuntime().updateLight.current?.();
    const cam = cameraForAspect(camera, SCREENSHOT_WIDTH / SCREENSHOT_HEIGHT);
    if (!viewport.captureInto(ctx, scene, cam, SCREENSHOT_WIDTH, SCREENSHOT_HEIGHT)) return false;
    download(canvas);
    return true;
}

export async function takeScreenshot() {
    let renderer = getViewerRuntime().renderer.current;
    if (!renderer) {
        console.error("Renderer not found");
        return;
    }
    let scene = getViewerRuntime().scene.current;
    if (!scene) {
        console.error("Scene not found");
        return;
    }
    let camera = getViewerRuntime().camera.current;
    if (!camera) {
        console.error("Camera not found");
        return;
    }

    if (takeAdacppScreenshot(scene, camera)) return;
    await takeHighResScreenshot(renderer, scene, camera, false);
}


export async function takeHighResScreenshot(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
    transparentBackground = false // <-- optional flag
) {
    const width = SCREENSHOT_WIDTH;
    const height = SCREENSHOT_HEIGHT;

    const renderTarget = new THREE.WebGLRenderTarget(width, height, {
        type: THREE.UnsignedByteType,
        // As the screen: without it the picture is read back in linear colour, and comes out dark.
        colorSpace: THREE.SRGBColorSpace,
    });

    const originalRenderTarget = renderer.getRenderTarget();
    const originalSize = new THREE.Vector2();
    renderer.getSize(originalSize);

    if (camera instanceof THREE.PerspectiveCamera) {
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }
    if (getViewerRuntime().updateLight.current)
        getViewerRuntime().updateLight.current!()
    renderer.setSize(width, height);
    renderer.setRenderTarget(renderTarget);
    renderer.render(scene, camera);

    const pixelBuffer = new Uint8Array(width * height * 4);
    renderer.readRenderTargetPixels(renderTarget, 0, 0, width, height, pixelBuffer);

    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = width;
    tempCanvas.height = height;
    const ctx = tempCanvas.getContext('2d');
    if (!ctx) {
        throw new Error('Failed to get 2D context');
    }

    const imageData = ctx.createImageData(width, height);

    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const srcIndex = ((height - y - 1) * width + x) * 4;
            const destIndex = (y * width + x) * 4;
            const r = pixelBuffer[srcIndex];
            const g = pixelBuffer[srcIndex + 1];
            const b = pixelBuffer[srcIndex + 2];
            const a = pixelBuffer[srcIndex + 3];

            if (a > 0) {
                imageData.data[destIndex] = Math.min(255, Math.round(r * 255 / a));
                imageData.data[destIndex + 1] = Math.min(255, Math.round(g * 255 / a));
                imageData.data[destIndex + 2] = Math.min(255, Math.round(b * 255 / a));
            } else {
                imageData.data[destIndex] = 0;
                imageData.data[destIndex + 1] = 0;
                imageData.data[destIndex + 2] = 0;
            }

            imageData.data[destIndex + 3] = a;
        }
    }

    ctx.putImageData(imageData, 0, 0);

    download(tempCanvas);

    renderer.setRenderTarget(originalRenderTarget);
    renderer.setSize(originalSize.x, originalSize.y);

    if (camera instanceof THREE.PerspectiveCamera) {
        camera.aspect = originalSize.x / originalSize.y;
        camera.updateProjectionMatrix();
    }

    renderTarget.dispose();
}
