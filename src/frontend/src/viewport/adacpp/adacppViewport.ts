// The experimental adacpp WebGPU renderer as a drop-in for the main viewport draw.
//
// three.js stays the scene graph: loading, the tree, selection, controls and the orientation gizmo are
// unchanged. Picking goes through viewport/picking.ts, which asks this renderer while it draws. Each frame
// this mirrors what three.js would have drawn into the adacpp renderer (wasm, /wasm/adacpp_render.js)
// and draws it on a WebGPU canvas layered over the hidden WebGL one. The renderer itself lives in
// adacpp (src/render). Section caps are the renderer's own (three.js' stencil helpers are not mirrored);
// gizmo handles and the face highlight are mirrored like any other object.
//
// Sprites (canvas-textured labels, markers, icons) are drawn by a small transparent three.js renderer on
// a canvas above, over everything, unless the `adacppLabels` switch hands them to adacpp, which tests
// them against the model's depth.
import * as THREE from "three";
import {create} from "zustand";
import {useOptionsStore} from "@/state/optionsStore";
import {adacppHandover} from "@/state/perfStore";
import {resolveModuleUrl} from "@/utils/wasm/emscriptenLoader";
import {SceneSync, SPRITE_LAYER} from "./sceneSync";
import type {AdacppModule, AdacppStats, GpuDeviceLike, GpuLike, RawPick, WebRendererApi} from "./types";

const MODULE_URL = "/wasm/adacpp_render.js";

/** The renderer inlined as base64 by embed-script.cjs. The single-file viewer opens from
 *  file://, where there is no /wasm to import from. */
interface InlineRenderer {
    js: string;
    wasm: string;
}

function decodeBase64(b64: string): Uint8Array {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
}

type ModuleFactory = (opts: object) => Promise<AdacppModule>;

/** Start the module. A failed `instantiateWasm` never settles the factory's own promise, so it is raced
 *  against `instantiationFailed`: the caller gets the reason instead of waiting for ever. */
export function startRendererModule(
    factory: ModuleFactory,
    opts: object,
    instantiationFailed: Promise<never> | null,
): Promise<AdacppModule> {
    const started = factory(opts);
    return instantiationFailed ? Promise.race([started, instantiationFailed]) : started;
}

/** The module factory and the options it needs: from the inlined copy when the page has one
 *  (the wasm instantiated through `instantiateWasm`, since a blob-imported module cannot locate its
 *  own and this build reads no `wasmBinary`), else from /wasm. `instantiationFailed` rejects when the
 *  inlined wasm cannot be instantiated. */
async function loadRendererModule(): Promise<{factory: ModuleFactory; opts: object; instantiationFailed: Promise<never> | null}> {
    const inline = (globalThis as {ADACPP_RENDER_INLINE?: InlineRenderer}).ADACPP_RENDER_INLINE;
    if (inline) {
        const url = URL.createObjectURL(new Blob([decodeBase64(inline.js)], {type: "text/javascript"}));
        try {
            const mod = await import(/* @vite-ignore */ url);
            // Compiled here, so a bad module throws to the caller rather than hanging the factory.
            const compiled = await WebAssembly.compile(decodeBase64(inline.wasm) as BufferSource);
            let fail: (err: unknown) => void = () => {};
            const instantiationFailed = new Promise<never>((_, reject) => (fail = reject));
            instantiationFailed.catch(() => {}); // observed by the race in startRendererModule
            const instantiateWasm = (
                imports: WebAssembly.Imports,
                receive: (instance: WebAssembly.Instance, module: WebAssembly.Module) => void,
            ) => {
                void WebAssembly.instantiate(compiled, imports).then(
                    (instance) => receive(instance, compiled),
                    (err) => fail(new Error(`instantiating the inlined renderer failed: ${err instanceof Error ? err.message : String(err)}`)),
                );
                return {};
            };
            return {factory: mod.default ?? mod, opts: {instantiateWasm}, instantiationFailed};
        } finally {
            URL.revokeObjectURL(url);
        }
    }
    const mod = await import(/* @vite-ignore */ resolveModuleUrl(MODULE_URL, globalThis.location?.origin));
    return {factory: mod.default ?? mod, opts: {}, instantiationFailed: null};
}

export type AdacppRenderState = "off" | "starting" | "active" | "failed";

interface AdacppRenderStatus {
    state: AdacppRenderState;
    error: string | null;
    adapter: string | null;
    stats: AdacppStats | null;
    /** Why adacpp is not drawing the labels although asked to (the three.js pass draws them instead). */
    labelsError: string | null;
}

/** What the Performance panel shows about the adacpp renderer. Not persisted. */
export const useAdacppRenderStatus = create<AdacppRenderStatus>(() => ({
    state: "off",
    error: null,
    adapter: null,
    stats: null,
    labelsError: null,
}));

// The state on the document element too (`data-adacpp-renderer`, with the failure reason beside it), so
// an end-to-end test can tell a WebGPU frame from a silent fallback to three.js.
const rootData = typeof document !== "undefined" ? document.documentElement?.dataset : undefined;
if (rootData) {
    const publish = (st: AdacppRenderStatus) => {
        rootData.adacppRenderer = st.state;
        if (st.error) rootData.adacppRendererError = st.error;
        else delete rootData.adacppRendererError;
    };
    publish(useAdacppRenderStatus.getState());
    useAdacppRenderStatus.subscribe(publish);
}

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
    private culling = {frustum: true, cone: true, occlusion: true};

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

            const {factory, opts, instantiationFailed} = await loadRendererModule();
            const module = await startRendererModule(factory, {...opts, preinitializedWebGPUDevice: device}, instantiationFailed);

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
        this.culling = culling;
        const w = Math.max(1, Math.floor(this.cssWidth * pixelRatio));
        const h = Math.max(1, Math.floor(this.cssHeight * pixelRatio));
        if (!this.draw(scene, camera, w, h, pixelRatio)) return;
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

    /** Sprites to adacpp when the switch says so and it has not failed at it; a failure is shown in the
     *  Performance panel and the three.js pass draws them again. */
    private labelsWanted = false;
    private syncLabelsMode(wanted: boolean): void {
        if (wanted !== this.labelsWanted) {
            this.labelsWanted = wanted;
            this.sync.nativeSpritesError = null;
            this.sync.nativeSprites = wanted;
            useAdacppRenderStatus.setState({labelsError: null});
        }
        const error = this.sync.nativeSpritesError;
        if (error && useAdacppRenderStatus.getState().labelsError !== error) useAdacppRenderStatus.setState({labelsError: error});
    }

    /** One frame at `w` x `h` device pixels. False when it failed: the viewport is then dead and three.js
     *  takes over (the status carries the reason). */
    private draw(scene: THREE.Scene, camera: THREE.Camera, w: number, h: number, pixelRatio: number): boolean {
        if (this.canvas.width !== w || this.canvas.height !== h) {
            this.canvas.width = w;
            this.canvas.height = h;
        }
        try {
            this.renderer.resize(w, h);
            this.renderer.setCulling(this.culling.frustum, this.culling.cone, this.culling.occlusion);
            const handover = adacppHandover();
            this.sync.pickPoints = handover.pointPick && useOptionsStore.getState().useGpuPointPicking;
            this.syncLabelsMode(handover.labels);
            this.sync.sync(scene, camera, w, h, pixelRatio);
            this.renderer.render();
            this.renderSprites(scene, camera, w, h);
            return true;
        } catch (err) {
            this.alive_ = false;
            const message = err instanceof Error ? err.message : String(err);
            console.error("[adacpp-webgpu] render failed; back to three.js", err);
            useAdacppRenderStatus.setState({state: "failed", error: message});
            return false;
        }
    }

    /** Draw one frame at `width` x `height` pixels through `camera` and copy it into `target` (scaled to
     *  the target canvas), for screenshots and animation export. Rendered and copied in one task: a
     *  canvas' WebGPU image is only guaranteed until the browser composites. The next render() puts the
     *  on-screen size back. False when the renderer has gone. */
    captureInto(target: CanvasRenderingContext2D, scene: THREE.Scene, camera: THREE.Camera, width: number, height: number): boolean {
        if (!this.alive_) return false;
        const w = Math.max(1, Math.floor(width)), h = Math.max(1, Math.floor(height));
        if (!this.draw(scene, camera, w, h, 1)) return false;
        const out = target.canvas;
        target.drawImage(this.canvas, 0, 0, w, h, 0, 0, out.width, out.height);
        const sprites = this.spriteRenderer?.domElement;
        if (sprites && sprites.style.display !== "none") target.drawImage(sprites, 0, 0, w, h, 0, 0, out.width, out.height);
        return true;
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
