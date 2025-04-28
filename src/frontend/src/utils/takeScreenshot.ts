import * as THREE from 'three';
import {cameraRef, rendererRef, sceneRef, updatelightRef} from "../state/refs";

export async function takeScreenshot() {
    let renderer = rendererRef.current;
    if (!renderer) {
        console.error("Renderer not found");
        return;
    }
    let scene = sceneRef.current;
    if (!scene) {
        console.error("Scene not found");
        return;
    }
    let camera = cameraRef.current;
    if (!camera) {
        console.error("Camera not found");
        return;
    }

    await takeHighResScreenshot(renderer, scene, camera, false);
}


export async function takeHighResScreenshot(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
    transparentBackground = false // <-- optional flag
) {
    const width = 1920;
    const height = 1080;

    const renderTarget = new THREE.WebGLRenderTarget(width, height, {
        type: THREE.UnsignedByteType,
        // format: THREE.RGBAFormat,
    });

    const originalRenderTarget = renderer.getRenderTarget();
    const originalSize = new THREE.Vector2();
    renderer.getSize(originalSize);

    if (camera instanceof THREE.PerspectiveCamera) {
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }
    if (updatelightRef.current)
        updatelightRef.current()
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

    tempCanvas.toBlob(blob => {
        if (blob) {
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = 'screenshot.png';
            link.click();
        }
    });

    renderer.setRenderTarget(originalRenderTarget);
    renderer.setSize(originalSize.x, originalSize.y);

    if (camera instanceof THREE.PerspectiveCamera) {
        camera.aspect = originalSize.x / originalSize.y;
        camera.updateProjectionMatrix();
    }

    renderTarget.dispose();
}
