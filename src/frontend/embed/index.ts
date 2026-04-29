// Embedded ada-py viewer — minimal three.js GLB renderer with the API
// paradoc's `vendor/ada-viewer/` placeholder locked in:
//
//   mountViewer(element, { modelBytes, camera, onReady?, onError? }):
//       { dispose() }
//
// Pure component: no globals, no fetch, no WebSocket. Receives glb bytes
// in and renders pixels out. Deliberately not coupled to the rest of the
// adapy frontend (zustand stores, refs, comms) — those would balloon the
// bundle and break the embed contract.
//
// The full app at viewer.krande.no still uses the shared sceneHelpers/*;
// this file is only consumed by `npm run build:embed` (vite.config.embed.ts).

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export interface CameraPreset {
    name: string;
    azimuth_deg: number;
    elevation_deg: number;
    roll_deg?: number;
    target?: 'bbox_center';
    distance?: 'fit' | number;
    fov_deg?: number;
    margin?: number;
}

export interface MountViewerOptions {
    modelBytes: Uint8Array;
    camera: CameraPreset;
    caption?: string;
    onReady?: () => void;
    onError?: (err: Error) => void;
}

export interface MountedViewer {
    dispose: () => void;
}

const DEFAULT_FOV = 50;
const DEFAULT_MARGIN = 1.15;
const DEFAULT_MIN_HEIGHT = 400;

export function mountViewer(element: HTMLElement, opts: MountViewerOptions): MountedViewer {
    let disposed = false;

    // --- DOM container ---
    element.innerHTML = '';
    if (!element.style.minHeight) element.style.minHeight = `${DEFAULT_MIN_HEIGHT}px`;
    element.style.position = 'relative';
    element.style.overflow = 'hidden';

    // --- Renderer / scene / camera ---
    const fov = opts.camera.fov_deg ?? DEFAULT_FOV;
    const initialWidth = Math.max(element.clientWidth, 320);
    const initialHeight = Math.max(element.clientHeight, DEFAULT_MIN_HEIGHT);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(initialWidth, initialHeight, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    element.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf8fafc); // tailwind slate-50

    const camera = new THREE.PerspectiveCamera(fov, initialWidth / initialHeight, 0.01, 10000);

    // --- Lighting ---
    // Hemisphere fakes ambient skylight; the directional light gives definition.
    scene.add(new THREE.HemisphereLight(0xffffff, 0xb1b8c4, 0.85));
    const sun = new THREE.DirectionalLight(0xffffff, 0.9);
    sun.position.set(5, 10, 7.5);
    scene.add(sun);

    // --- Controls ---
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    // --- Resize handling ---
    const onResize = () => {
        const w = element.clientWidth || initialWidth;
        const h = Math.max(element.clientHeight, DEFAULT_MIN_HEIGHT);
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(onResize);
    ro.observe(element);

    // --- Render loop ---
    let frame = 0;
    const tick = () => {
        if (disposed) return;
        frame = requestAnimationFrame(tick);
        controls.update();
        renderer.render(scene, camera);
    };

    // --- Load the GLB ---
    let loadedRoot: THREE.Object3D | null = null;
    try {
        const loader = new GLTFLoader();
        // Copy into a fresh ArrayBuffer — the input may be a view into a
        // larger buffer (e.g. fetched WS frame) and GLTFLoader.parse trusts
        // the whole buffer.
        const buf = new ArrayBuffer(opts.modelBytes.byteLength);
        new Uint8Array(buf).set(opts.modelBytes);

        loader.parse(
            buf,
            '',
            (gltf) => {
                if (disposed) return;
                loadedRoot = gltf.scene;
                scene.add(loadedRoot);
                applyCameraPreset(camera, controls, loadedRoot, opts.camera);
                tick();
                queueMicrotask(() => {
                    if (!disposed) opts.onReady?.();
                });
            },
            (err) => {
                opts.onError?.(err instanceof Error ? err : new Error(String(err)));
            }
        );
    } catch (err) {
        opts.onError?.(err instanceof Error ? err : new Error(String(err)));
    }

    return {
        dispose() {
            if (disposed) return;
            disposed = true;
            cancelAnimationFrame(frame);
            ro.disconnect();
            controls.dispose();
            if (loadedRoot) {
                scene.remove(loadedRoot);
                disposeObject(loadedRoot);
            }
            renderer.dispose();
            try {
                element.removeChild(renderer.domElement);
            } catch {
                /* element already detached */
            }
        },
    };
}

function applyCameraPreset(
    camera: THREE.PerspectiveCamera,
    controls: OrbitControls,
    root: THREE.Object3D,
    preset: CameraPreset
): void {
    const box = new THREE.Box3().setFromObject(root);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() * 0.5, 1e-3);

    const margin = preset.margin ?? DEFAULT_MARGIN;
    const fovRad = (camera.fov * Math.PI) / 180;
    let distance: number;
    if (typeof preset.distance === 'number') {
        distance = preset.distance;
    } else {
        // Fit the bounding sphere in the smaller of the two view dimensions.
        const fitV = radius / Math.sin(fovRad / 2);
        const fitH = radius / Math.sin(Math.atan(Math.tan(fovRad / 2) * camera.aspect));
        distance = Math.max(fitV, fitH) * margin;
    }

    const az = (preset.azimuth_deg * Math.PI) / 180;
    const el = (preset.elevation_deg * Math.PI) / 180;
    const offset = new THREE.Vector3(
        distance * Math.cos(el) * Math.sin(az),
        distance * Math.sin(el),
        distance * Math.cos(el) * Math.cos(az)
    );
    camera.position.copy(center).add(offset);
    camera.up.set(0, 1, 0);
    camera.lookAt(center);

    if (preset.roll_deg) {
        const forward = new THREE.Vector3().subVectors(center, camera.position).normalize();
        camera.up.applyAxisAngle(forward, (preset.roll_deg * Math.PI) / 180);
        camera.lookAt(center);
    }

    // Tighten near/far to the model so depth precision stays usable.
    camera.near = Math.max(distance / 1000, 1e-3);
    camera.far = distance * 100;
    camera.updateProjectionMatrix();

    controls.target.copy(center);
    controls.minDistance = distance * 0.05;
    controls.maxDistance = distance * 20;
    controls.update();
}

function disposeObject(obj: THREE.Object3D): void {
    obj.traverse((node) => {
        const mesh = node as THREE.Mesh;
        if (mesh.isMesh) {
            mesh.geometry?.dispose();
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
            for (const m of mats) {
                if (!m) continue;
                for (const key of Object.keys(m) as (keyof THREE.Material)[]) {
                    const val = (m as any)[key];
                    if (val && (val as THREE.Texture).isTexture) {
                        (val as THREE.Texture).dispose();
                    }
                }
                m.dispose();
            }
        }
    });
}
