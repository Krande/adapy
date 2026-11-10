// The three.js-free viewport's scene: a GLB read by adacpp's own reader, drawn by its WebGPU renderer,
// under an orbit camera, with pick, select and hide resolved per segment (one adapy object each). There
// is no THREE.Scene behind it: object identity comes from the GLB's JSON (embed-webgpu/glbObjects), and
// per-object state is segment state in the renderer. The WebGPU embed is the model for all of this; the
// difference is that this one lives in the viewer and takes its input from the viewport's input surface.
import {OrbitCamera, type Bounds} from "../../../embed-webgpu/camera";
import {glbJson, matchGeometries, objectPath, replayBatches, type SegmentObject} from "../../../embed-webgpu/glbObjects";
import {startRenderer} from "@/viewport/adacpp/rendererModule";
import {SegmentFlag, type AdacppModule, type GpuDeviceLike, type RawPick, type WebRendererApi} from "@/viewport/adacpp/types";

/** The viewer's background (#393939) and selection colour, as the three.js viewport has them. */
const BACKGROUND = 0x39 / 255;
const SELECTED = (0 | (0 << 8) | (255 << 16) | (255 << 24)) >>> 0;
const FEATURE_EDGE_ANGLE_DEG = 30;
const EDGE_COLOR: [number, number, number, number] = [0, 0, 0, 1];
/** A press released within this many pixels of where it went down is a click, not a drag. */
const TAP_SLOP_PX = 6;
const MAX_PIXEL_RATIO = 2;

export interface NativeObject {
    id: string;
    /** Its names from the root down; the last is its own. */
    path: string[];
}

let canvasSerial = 0;

export class NativeScene {
    readonly camera = new OrbitCamera();
    private readonly viewPtr: number;
    private readonly projPtr: number;
    private names = new Map<number, (SegmentObject | null)[]>();
    private bounds: Bounds | null = null;
    private objectList: NativeObject[] = [];
    private readonly selected = new Set<string>();
    private readonly hidden = new Set<string>();
    private dirty = true;
    private raf = 0;
    private disposed = false;
    private readonly cleanups: (() => void)[] = [];
    /** Selection, visibility or the object list changed. */
    onChange: (() => void) | null = null;
    /** The renderer stopped (device lost, a failed frame): the caller falls back. */
    onFailure: ((reason: string) => void) | null = null;
    /** Why it stopped, kept for a caller that attaches `onFailure` after the fact: a device can be lost
     *  before the first frame. */
    failure: string | null = null;

    private constructor(
        private readonly container: HTMLElement,
        private readonly input: HTMLElement,
        private readonly canvas: HTMLCanvasElement,
        private readonly module: AdacppModule,
        private readonly renderer: WebRendererApi,
        private readonly device: GpuDeviceLike,
    ) {
        this.viewPtr = module._malloc(64);
        this.projPtr = module._malloc(64);
        renderer.setBackground(BACKGROUND, BACKGROUND, BACKGROUND);
        const resize = new ResizeObserver(() => this.invalidate());
        resize.observe(container);
        this.cleanups.push(() => resize.disconnect());
        void device.lost.then((info) => {
            if (!this.disposed) this.fail(`WebGPU device lost: ${info.message}`);
        });
        this.bindInput();
    }

    /** Throws, with the reason, when WebGPU or the renderer cannot start. */
    static async create(container: HTMLElement, input: HTMLElement): Promise<NativeScene> {
        const {gpu, device, module} = await startRenderer("adacpp.native");
        device.onuncapturederror = (ev) => console.error("[adacpp-native]", ev.error.message);
        const canvas = document.createElement("canvas");
        canvas.id = `adacpp-native-canvas-${++canvasSerial}`;
        Object.assign(canvas.style, {position: "absolute", inset: "0", width: "100%", height: "100%", display: "block", pointerEvents: "none"});
        container.insertBefore(canvas, container.firstChild);
        const renderer = new module.WebRenderer(`#${canvas.id}`, gpu.getPreferredCanvasFormat());
        return new NativeScene(container, input, canvas, module, renderer, device);
    }

    /** Replace the model with a GLB. Throws when it has nothing to draw. */
    load(bytes: Uint8Array): void {
        this.renderer.clear();
        this.selected.clear();
        this.hidden.clear();
        const p = this.module._malloc(bytes.byteLength);
        this.module.HEAPU8.set(bytes, p);
        let loaded;
        try {
            loaded = this.renderer.loadGlb(p, bytes.byteLength, {featureEdgeAngleDeg: FEATURE_EDGE_ANGLE_DEG, lineColor: EDGE_COLOR, doubleSided: true});
        } finally {
            this.module._free(p);
        }
        if (!loaded.geometries.length) throw new Error("the model has nothing to draw");
        const json = glbJson(bytes);
        this.names = matchGeometries(json ? replayBatches(json) : [], loaded.geometries, (g) => this.renderer.segmentCount(g));
        const ids = new Set<string>();
        for (const segments of this.names.values()) for (const s of segments) if (s?.id) ids.add(s.id);
        this.objectList = [...ids].map((id) => {
            const path = json ? objectPath(json, id) : [];
            return {id, path: path.length ? path : [id]};
        }).sort((a, b) => a.path.join("/").localeCompare(b.path.join("/")));
        this.bounds = loaded.bounds;
        this.fit();
        this.onChange?.();
    }

    get objects(): readonly NativeObject[] {
        return this.objectList;
    }

    isSelected(id: string): boolean {
        return this.selected.has(id);
    }

    isHidden(id: string): boolean {
        return this.hidden.has(id);
    }

    get selectionSize(): number {
        return this.selected.size;
    }

    /** Frame the whole model. */
    fit(): void {
        if (!this.bounds) return;
        this.camera.frame(this.bounds, undefined, this.aspect());
        this.invalidate();
    }

    select(id: string | null, additive = false): void {
        if (!additive) this.selected.clear();
        if (id) {
            if (additive && this.selected.has(id)) this.selected.delete(id);
            else this.selected.add(id);
        }
        this.pushStates();
    }

    hideSelected(): void {
        for (const id of this.selected) this.hidden.add(id);
        this.selected.clear();
        this.pushStates();
    }

    setHidden(id: string, hidden: boolean): void {
        if (hidden) this.hidden.add(id);
        else this.hidden.delete(id);
        this.pushStates();
    }

    showAll(): void {
        this.hidden.clear();
        this.pushStates();
    }

    /** Every segment's state from the two sets: hidden wins over selected. */
    private pushStates(): void {
        for (const [instance, segments] of this.names) {
            const states = new Uint32Array(segments.length * 2);
            segments.forEach((s, i) => {
                if (!s?.id) return;
                if (this.hidden.has(s.id)) states[i * 2 + 1] = SegmentFlag.Hidden;
                else if (this.selected.has(s.id)) {
                    states[i * 2] = SELECTED;
                    states[i * 2 + 1] = SegmentFlag.Matte;
                }
            });
            const p = this.module._malloc(states.byteLength);
            this.module.HEAPU32.set(states, p >> 2);
            try {
                this.renderer.setSegmentStates(instance, p, segments.length);
            } finally {
                this.module._free(p);
            }
        }
        this.invalidate();
        this.onChange?.();
    }

    /** The object under a client position, in the frame last drawn. */
    async pick(clientX: number, clientY: number): Promise<string | null> {
        const rect = this.canvas.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return null;
        const x = Math.floor(((clientX - rect.left) / rect.width) * this.canvas.width);
        const y = Math.floor(((clientY - rect.top) / rect.height) * this.canvas.height);
        if (x < 0 || y < 0 || x >= this.canvas.width || y >= this.canvas.height) return null;
        const raw = await new Promise<RawPick | null>((resolve) => {
            try {
                this.renderer.pick(x, y, this.canvas.width, this.canvas.height, (r) => resolve(r.hit ? r : null));
            } catch (err) {
                console.warn("[adacpp-native] pick failed:", err);
                resolve(null);
            }
        });
        return raw ? (this.names.get(raw.instance)?.[raw.segment]?.id ?? null) : null;
    }

    invalidate(): void {
        this.dirty = true;
        if (!this.raf && !this.disposed) this.raf = requestAnimationFrame(() => this.tick());
    }

    private aspect(): number {
        return Math.max(1, this.container.clientWidth) / Math.max(1, this.container.clientHeight);
    }

    private tick(): void {
        this.raf = 0;
        if (this.disposed || !this.dirty) return;
        this.dirty = false;
        const ratio = Math.min(globalThis.devicePixelRatio || 1, MAX_PIXEL_RATIO);
        const w = Math.max(1, Math.floor(this.container.clientWidth * ratio));
        const h = Math.max(1, Math.floor(this.container.clientHeight * ratio));
        if (this.canvas.width !== w || this.canvas.height !== h) {
            this.canvas.width = w;
            this.canvas.height = h;
        }
        try {
            const {view, proj} = this.camera.matrices(this.aspect());
            this.module.HEAPF32.set(view, this.viewPtr >> 2);
            this.module.HEAPF32.set(proj, this.projPtr >> 2);
            this.renderer.resize(w, h);
            this.renderer.setCamera(this.viewPtr, this.projPtr, false);
            this.renderer.render();
        } catch (err) {
            this.fail(err instanceof Error ? err.message : String(err));
        }
    }

    private fail(reason: string): void {
        if (this.failure) return;
        this.failure = reason;
        this.onFailure?.(reason);
    }

    /** Orbit (drag), pan (right / middle drag, shift-drag, two fingers), zoom (wheel, pinch), click to select. */
    private bindInput(): void {
        const el = this.input;
        const pointers = new Map<number, {x: number; y: number}>();
        let lastPinch = 0;
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
            if (tap && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) > TAP_SLOP_PX) tap = null;
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
        on("pointerup", (e) => {
            const t = tap;
            tap = null;
            pointers.delete(e.pointerId);
            lastPinch = 0;
            if (t && t.id === e.pointerId) {
                const additive = e.ctrlKey || e.metaKey || e.shiftKey;
                void this.pick(e.clientX, e.clientY).then((id) => this.select(id, additive && !!id));
            }
        });
        on("pointercancel", (e) => {
            tap = null;
            pointers.delete(e.pointerId);
            lastPinch = 0;
        });
        on("contextmenu", (e) => e.preventDefault());
        on("wheel", (e) => {
            e.preventDefault();
            this.camera.zoom(Math.exp(e.deltaY * 0.001));
            this.invalidate();
        }, {passive: false});
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        if (this.raf) cancelAnimationFrame(this.raf);
        for (const c of this.cleanups) c();
        try {
            this.module._free(this.viewPtr);
            this.module._free(this.projPtr);
            this.renderer.delete();
            this.device.destroy();
        } catch (err) {
            console.warn("[adacpp-native] dispose:", err);
        }
        this.canvas.remove();
    }
}
