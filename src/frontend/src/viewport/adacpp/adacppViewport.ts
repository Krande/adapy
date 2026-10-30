// The experimental adacpp WebGPU renderer as a drop-in for the main viewport draw.
//
// three.js stays the scene graph: loading, the tree, selection, picking (GpuMeshPicker renders through
// the WebGLRenderer into its own target), controls and the orientation gizmo are unchanged. Each frame
// this mirrors what three.js would have drawn into the adacpp renderer (wasm, /wasm/adacpp_render.js)
// and draws it on a WebGPU canvas layered over the hidden WebGL one. The renderer itself lives in
// adacpp (src/render). Sprites (canvas-textured labels, markers, icons) are the exception: the renderer
// has no textures, so a small transparent three.js renderer draws just them on a canvas above.
//
// Not drawn yet (three.js-only for now): section caps, transform gizmos, face-highlight overlays; sprites
// draw over everything (no depth test against the model).
import * as THREE from "three";
import {create} from "zustand";
import {resolveModuleUrl} from "@/utils/wasm/emscriptenLoader";
import {SceneSync, SPRITE_LAYER} from "./sceneSync";
import type {AdacppModule, AdacppStats, GpuDeviceLike, GpuLike, RawPick, WebRendererApi} from "./types";

const MODULE_URL = "/wasm/adacpp_render.js";

export type AdacppRenderState = "off" | "starting" | "active" | "failed";

interface AdacppRenderStatus {
    state: AdacppRenderState;
    error: string | null;
    adapter: string | null;
    stats: AdacppStats | null;
}

/** What the Performance panel shows about the adacpp renderer. Not persisted. */
export const useAdacppRenderStatus = create<AdacppRenderStatus>(() => ({
    state: "off",
    error: null,
    adapter: null,
    stats: null,
}));

let canvasSerial = 0;

export class AdacppViewport {
    readonly canvas: HTMLCanvasElement;
    private readonly sync: SceneSync;
    private readonly resizeObserver: ResizeObserver;
    private alive_ = true;
    private lastStatsPush = 0;
    private cssWidth = 0;
    private cssHeight = 0;
    private spriteRenderer: THREE.WebGLRenderer | null = null;

    private constructor(
        private readonly module: AdacppModule,
        private readonly renderer: WebRendererApi,
        canvas: HTMLCanvasElement,
        private readonly device: GpuDeviceLike,
    ) {
        this.canvas = canvas;
        this.sync = new SceneSync(module, renderer);
        const host = canvas.parentElement!;
        this.cssWidth = host.clientWidth;
        this.cssHeight = host.clientHeight;
        this.resizeObserver = new ResizeObserver((entries) => {
            for (const e of entries) {
                this.cssWidth = e.contentRect.width;
                this.cssHeight = e.contentRect.height;
            }
        });
        this.resizeObserver.observe(host);
        void device.lost.then((info) => {
            if (!this.alive_) return;
            this.alive_ = false;
            useAdacppRenderStatus.setState({state: "failed", error: `WebGPU device lost: ${info.message}`});
        });
    }

    /** Throws when WebGPU or the module is unavailable; the caller keeps three.js drawing. */
    static async create(container: HTMLElement): Promise<AdacppViewport> {
        useAdacppRenderStatus.setState({state: "starting", error: null, stats: null});
        try {
            const gpu = (navigator as Navigator & {gpu?: GpuLike}).gpu;
            if (!gpu) throw new Error("this browser has no WebGPU (navigator.gpu is missing)");
            const adapter = await gpu.requestAdapter({powerPreference: "high-performance"});
            if (!adapter) throw new Error("no WebGPU adapter available");
            // Large merged CAD/FEM buffers exceed the 128 MiB default storage binding: take what the adapter has.
            const device = await adapter.requestDevice({
                label: "adacpp.render",
                requiredLimits: {
                    maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
                    maxBufferSize: adapter.limits.maxBufferSize,
                },
            });
            device.onuncapturederror = (ev) => console.error("[adacpp-webgpu]", ev.error.message);

            const origin = globalThis.location?.origin;
            const mod = await import(/* @vite-ignore */ resolveModuleUrl(MODULE_URL, origin));
            const factory = (mod.default ?? mod) as (opts: object) => Promise<AdacppModule>;
            const module = await factory({preinitializedWebGPUDevice: device});

            const canvas = document.createElement("canvas");
            canvas.id = `adacpp-webgpu-canvas-${++canvasSerial}`;
            canvas.dataset.adacppViewport = "true"; // setupControls must never bind to this one
            Object.assign(canvas.style, {
                position: "absolute",
                inset: "0",
                width: "100%",
                height: "100%",
                pointerEvents: "none", // input goes to the transparent WebGL canvas above it
                display: "block",
            });
            // First child so overlays appended later (gizmo, stats) stay on top.
            container.insertBefore(canvas, container.firstChild);
            const renderer = new module.WebRenderer(`#${canvas.id}`, gpu.getPreferredCanvasFormat());
            const info = adapter.info;
            useAdacppRenderStatus.setState({
                state: "active",
                adapter: [info?.vendor, info?.architecture, info?.description].filter(Boolean).join(" ") || "WebGPU",
            });
            return new AdacppViewport(module, renderer, canvas, device);
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            useAdacppRenderStatus.setState({state: "failed", error: message});
            throw err;
        }
    }

    get alive(): boolean {
        return this.alive_;
    }

    get lastStats(): AdacppStats | null {
        return useAdacppRenderStatus.getState().stats;
    }

    render(
        scene: THREE.Scene,
        camera: THREE.Camera,
        pixelRatio: number,
        culling: {frustum: boolean; cone: boolean; occlusion: boolean},
    ) {
        if (!this.alive_) return;
        const w = Math.max(1, Math.floor(this.cssWidth * pixelRatio));
        const h = Math.max(1, Math.floor(this.cssHeight * pixelRatio));
        if (this.canvas.width !== w || this.canvas.height !== h) {
            this.canvas.width = w;
            this.canvas.height = h;
        }
        try {
            this.renderer.resize(w, h);
            this.renderer.setCulling(culling.frustum, culling.cone, culling.occlusion);
            this.sync.sync(scene, camera, w, h, pixelRatio);
            this.renderer.render();
            this.renderSprites(scene, camera, w, h);
        } catch (err) {
            this.alive_ = false;
            const message = err instanceof Error ? err.message : String(err);
            console.error("[adacpp-webgpu] render failed; back to three.js", err);
            useAdacppRenderStatus.setState({state: "failed", error: message});
            return;
        }
        const now = performance.now();
        if (now - this.lastStatsPush > 250) {
            this.lastStatsPush = now;
            const syncMs = this.sync.lastSyncMs;
            const st = this.renderer.stats();
            if (st.gpuCulled) {
                // The drawn / occluded counts are on the GPU: read them back (they land a frame or so later).
                this.renderer.readCullStats((full) => {
                    if (this.alive_) useAdacppRenderStatus.setState({stats: {...full, syncMs}});
                });
            } else {
                useAdacppRenderStatus.setState({stats: {...st, syncMs}});
            }
        }
    }

    /** Draw the scene's sprites (SceneSync put them on SPRITE_LAYER) with three.js, over the WebGPU image. */
    private renderSprites(scene: THREE.Scene, camera: THREE.Camera, w: number, h: number): void {
        if (this.sync.spriteCount === 0) {
            if (this.spriteRenderer) this.spriteRenderer.domElement.style.display = "none";
            return;
        }
        let sr = this.spriteRenderer;
        if (!sr) {
            sr = this.spriteRenderer = new THREE.WebGLRenderer({alpha: true, antialias: true});
            sr.setClearColor(0x000000, 0);
            sr.localClippingEnabled = true; // sprites may carry the section planes (clippingPlanes)
            Object.assign(sr.domElement.style, {
                position: "absolute",
                inset: "0",
                width: "100%",
                height: "100%",
                pointerEvents: "none",
            });
            this.canvas.after(sr.domElement);
        }
        sr.domElement.style.display = "block";
        if (sr.domElement.width !== w || sr.domElement.height !== h) sr.setSize(w, h, false);
        const background = scene.background;
        const layers = camera.layers.mask;
        scene.background = null;
        camera.layers.set(SPRITE_LAYER);
        try {
            sr.render(scene, camera);
        } finally {
            scene.background = background;
            camera.layers.mask = layers;
        }
    }

    /** GPU pick at a client (CSS px) position, in the frame last drawn. */
    pickRaw(clientX: number, clientY: number): Promise<RawPick | null> {
        if (!this.alive_) return Promise.resolve(null);
        const rect = this.canvas.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return Promise.resolve(null);
        const x = Math.floor(((clientX - rect.left) / rect.width) * this.canvas.width);
        const y = Math.floor(((clientY - rect.top) / rect.height) * this.canvas.height);
        if (x < 0 || y < 0 || x >= this.canvas.width || y >= this.canvas.height) return Promise.resolve(null);
        return new Promise((resolve) => {
            try {
                this.renderer.pick(x, y, this.canvas.width, this.canvas.height, (r) => resolve(r.hit ? r : null));
            } catch (err) {
                console.warn("[adacpp-webgpu] pick failed:", err);
                resolve(null);
            }
        });
    }

    /** The scene object an adacpp instance draws (see SceneSync.entryForInstance). */
    entryForInstance(instance: number) {
        return this.sync.entryForInstance(instance);
    }

    dispose(): void {
        this.alive_ = false;
        this.resizeObserver.disconnect();
        this.sync.dispose();
        this.renderer.delete();
        this.canvas.remove();
        this.spriteRenderer?.dispose();
        this.spriteRenderer?.domElement.remove();
        this.device.destroy();
        useAdacppRenderStatus.setState({state: "off", stats: null});
    }
}
