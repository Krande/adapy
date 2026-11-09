// One WebGPU figure: a canvas in the host element, adacpp's renderer drawing into it, an orbit camera
// driven by pointer / wheel / touch, and a frame rendered only when something changed. When WebGPU or
// the renderer is unavailable, or loading fails, the figure shows its poster instead.
import type {CameraPreset} from "../embed/index.d";
import type {AdacppModule, LoadGlbOptions, LoadedGlb, RawPick, WebRendererApi} from "../src/viewport/adacpp/types";
import {type Bounds, OrbitCamera} from "./camera";
import {createRenderer, type GpuContext, webGpuUnavailableReason, withBytes, withHeap} from "./renderer";

/** Ada Studio's embed background (#f8fafc), linear. */
const BACKGROUND = srgbToLinear([0xf8 / 255, 0xfa / 255, 0xfc / 255]);

export function srgbToLinear(c: number[]): number[] {
    return c.map((v) => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
}

export interface FallbackOptions {
    /** The figure's static poster, shown when the 3D view cannot run. */
    posterUrl?: string;
    caption?: string;
}

/** The class of the poster / note shown instead of the 3D view, so a host (or a test) can tell the
 *  figure mounted in its fallback state; the reason is in its `data-reason`. */
export const FALLBACK_CLASS = "ada-viewer-fallback";

/** Show the poster (or a note) in `element` instead of the 3D view. */
export function showFallback(element: HTMLElement, opts: FallbackOptions, reason: string): void {
    element.innerHTML = "";
    if (opts.posterUrl) {
        // The poster, with the reason on it: without it the fallback looks exactly like the static
        // figure, and a reader (on a phone, no tooltip) cannot tell the 3D view did not start.
        const wrap = document.createElement("div");
        wrap.className = FALLBACK_CLASS;
        wrap.dataset.reason = reason;
        wrap.style.cssText = "position:relative;width:100%;";
        const img = document.createElement("img");
        img.src = opts.posterUrl;
        img.alt = opts.caption || "3D figure";
        img.title = `3D view unavailable: ${reason}`;
        img.style.cssText = "display:block;max-width:100%;height:auto;margin:auto;";
        const note = document.createElement("div");
        note.textContent = `3D view unavailable: ${reason}`;
        note.style.cssText =
            "position:absolute;left:6px;bottom:6px;right:6px;width:fit-content;max-width:calc(100% - 12px);" +
            "background:rgba(30,41,59,0.85);color:#f8fafc;font:11px/1.3 system-ui;padding:3px 7px;" +
            "border-radius:4px;pointer-events:none;";
        wrap.append(img, note);
        element.appendChild(wrap);
        return;
    }
    const note = document.createElement("div");
    note.className = FALLBACK_CLASS;
    note.dataset.reason = reason;
    note.style.cssText =
        "display:flex;align-items:center;justify-content:center;width:100%;min-height:200px;" +
        "color:#64748b;font:13px system-ui;text-align:center;padding:1em;";
    note.textContent = `3D view unavailable: ${reason}`;
    element.appendChild(note);
}

export class Viewer {
    readonly camera = new OrbitCamera();
    readonly host: HTMLDivElement;
    readonly canvas: HTMLCanvasElement;
    private ctx: GpuContext | null = null;
    private dirty = true;
    private raf = 0;
    private disposed = false;
    private readonly resizeObserver: ResizeObserver;
    private readonly cleanups: (() => void)[] = [];
    private readonly matPtr: {view: number; proj: number} = {view: 0, proj: 0};
    /** The GPU cannot draw this figure (a compatibility-mode device reported an error). */
    onGpuFailure: ((message: string) => void) | null = null;
    /** A tap (no drag) at a client position: the mount picks there. */
    onTap: ((clientX: number, clientY: number) => void) | null = null;
    /** Called before each frame (an animation drives the model from here); return true to keep going. */
    onFrame: ((t: number) => boolean) | null = null;

    constructor(element: HTMLElement) {
        element.innerHTML = "";
        this.host = document.createElement("div");
        this.host.style.cssText = "position:relative;width:100%;height:100%;min-height:200px;touch-action:none;";
        this.canvas = document.createElement("canvas");
        this.canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;";
        this.host.appendChild(this.canvas);
        element.appendChild(this.host);
        this.resizeObserver = new ResizeObserver(() => this.invalidate());
        this.resizeObserver.observe(this.host);
        this.bindInput();
    }

    get module(): AdacppModule {
        return this.ctx!.module;
    }

    get renderer(): WebRendererApi {
        return this.ctx!.renderer;
    }

    /** Start WebGPU and the renderer; throws when either is unavailable. */
    async start(): Promise<void> {
        const why = webGpuUnavailableReason();
        if (why) throw new Error(why);
        this.ctx = await createRenderer(this.canvas);
        if (this.disposed) {
            this.release();
            throw new Error("disposed");
        }
        this.ctx.onFatalError((m) => this.onGpuFailure?.(m));
        const [r, g, b] = BACKGROUND;
        this.ctx.renderer.setBackground(r, g, b);
        this.matPtr.view = this.ctx.module._malloc(64);
        this.matPtr.proj = this.ctx.module._malloc(64);
        void this.ctx.device.lost.then((info) => {
            if (!this.disposed) console.error("[ada-viewer-webgpu] device lost:", info.message);
        });
    }

    /** A GLB through adacpp's reader (the posters' own); frames nothing (see `frame`). */
    loadGlb(bytes: Uint8Array, opts: LoadGlbOptions): LoadedGlb {
        return withBytes(this.module, bytes, (p) => this.renderer.loadGlb(p, bytes.byteLength, opts));
    }

    /** Copy arrays to the heap for one call (see renderer.withHeap). */
    heap<T>(arrays: (Float32Array | Uint32Array | null)[], fn: (ptrs: number[]) => T): T {
        return withHeap(this.module, arrays, fn);
    }

    frame(bounds: Bounds, preset: CameraPreset | undefined) {
        const {w, h} = this.size();
        this.camera.frame(bounds, preset, w / h);
        this.invalidate();
    }

    invalidate() {
        this.dirty = true;
        if (!this.raf && !this.disposed) this.raf = requestAnimationFrame((t) => this.tick(t));
    }

    private size() {
        const w = Math.max(1, this.host.clientWidth), h = Math.max(1, this.host.clientHeight);
        return {w, h};
    }

    private tick(t: number) {
        this.raf = 0;
        if (this.disposed || !this.ctx) return;
        const animating = this.onFrame ? this.onFrame(t) : false;
        if (!this.dirty && !animating) return;
        this.dirty = false;
        const dpr = Math.min(globalThis.devicePixelRatio || 1, 2);
        const {w, h} = this.size();
        const pw = Math.floor(w * dpr), ph = Math.floor(h * dpr);
        if (this.canvas.width !== pw || this.canvas.height !== ph) {
            this.canvas.width = pw;
            this.canvas.height = ph;
        }
        const {view, proj} = this.camera.matrices(w / h);
        const heap = this.module.HEAPF32;
        heap.set(view, this.matPtr.view >> 2);
        heap.set(proj, this.matPtr.proj >> 2);
        this.renderer.resize(pw, ph);
        this.renderer.setCamera(this.matPtr.view, this.matPtr.proj, false);
        this.renderer.render();
        if (animating) this.invalidate();
    }

    private bindInput() {
        const el = this.host;
        const pointers = new Map<number, {x: number; y: number}>();
        let lastPinch = 0;
        // A tap: one pointer, released within a few pixels of where it went down.
        let tap: {id: number; x: number; y: number} | null = null;
        const on = <K extends keyof HTMLElementEventMap>(type: K, fn: (e: HTMLElementEventMap[K]) => void, opts?: AddEventListenerOptions) => {
            el.addEventListener(type, fn as EventListener, opts);
            this.cleanups.push(() => el.removeEventListener(type, fn as EventListener, opts));
        };
        on("pointerdown", (e) => {
            el.setPointerCapture(e.pointerId);
            pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
            lastPinch = 0;
            tap = pointers.size === 1 && e.button === 0 ? {id: e.pointerId, x: e.clientX, y: e.clientY} : null;
        });
        on("pointermove", (e) => {
            const prev = pointers.get(e.pointerId);
            if (!prev) return;
            const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
            if (tap && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) > 6) tap = null;
            pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
            const h = el.clientHeight;
            if (pointers.size >= 2) {
                const [a, b] = [...pointers.values()];
                const d = Math.hypot(a.x - b.x, a.y - b.y);
                if (lastPinch > 0 && d > 0) this.camera.zoom(lastPinch / d);
                lastPinch = d;
                this.camera.pan(dx / 2, dy / 2, h);
            } else if (e.buttons & 2 || e.buttons & 4 || e.shiftKey) {
                this.camera.pan(dx, dy, h);
            } else {
                this.camera.orbit(dx, dy, h);
            }
            this.invalidate();
        });
        const up = (e: PointerEvent) => {
            pointers.delete(e.pointerId);
            lastPinch = 0;
        };
        on("pointerup", (e) => {
            const t = tap;
            tap = null;
            up(e);
            if (t && t.id === e.pointerId) this.onTap?.(e.clientX, e.clientY);
        });
        on("pointercancel", (e) => {
            tap = null;
            up(e);
        });
        on("contextmenu", (e) => e.preventDefault());
        on("wheel", (e) => {
            e.preventDefault();
            this.camera.zoom(Math.exp(e.deltaY * 0.001));
            this.invalidate();
        }, {passive: false});
        on("dblclick", () => {
            // back to the preset framing
            this.onReframe?.();
        });
    }

    /** GPU pick at a client (CSS px) position, in the frame last drawn; null for a miss. */
    pick(clientX: number, clientY: number): Promise<RawPick | null> {
        if (!this.ctx) return Promise.resolve(null);
        const rect = this.canvas.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return Promise.resolve(null);
        const x = Math.floor(((clientX - rect.left) / rect.width) * this.canvas.width);
        const y = Math.floor(((clientY - rect.top) / rect.height) * this.canvas.height);
        if (x < 0 || y < 0 || x >= this.canvas.width || y >= this.canvas.height) return Promise.resolve(null);
        return new Promise((resolve) => {
            try {
                this.renderer.pick(x, y, this.canvas.width, this.canvas.height, (r) => resolve(r.hit ? r : null));
            } catch (err) {
                console.warn("[ada-viewer-webgpu] pick failed:", err);
                resolve(null);
            }
        });
    }

    /** Per-segment colour overrides on an instance: `states` is (packed RGBA8 colour, SegmentFlag) pairs. */
    setSegmentStates(instance: number, states: Uint32Array): void {
        this.heap([states], ([p]) => this.renderer.setSegmentStates(instance, p, states.length / 2));
        this.invalidate();
    }

    /** Set by the mount: re-frame the model (double-click). */
    onReframe: (() => void) | null = null;

    private release() {
        try {
            if (this.ctx) {
                if (this.matPtr.view) this.ctx.module._free(this.matPtr.view);
                if (this.matPtr.proj) this.ctx.module._free(this.matPtr.proj);
                this.ctx.renderer.delete();
                this.ctx.device.destroy();
            }
        } catch {
            /* best effort */
        }
        this.ctx = null;
    }

    dispose() {
        if (this.disposed) return;
        this.disposed = true;
        if (this.raf) cancelAnimationFrame(this.raf);
        this.resizeObserver.disconnect();
        for (const c of this.cleanups) c();
        this.release();
        this.host.remove();
    }
}
