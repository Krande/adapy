// The three.js-free viewport's scene: a GLB read by adacpp's own reader, drawn by its WebGPU renderer
// under the renderer's own camera, with pick, select and hide resolved per segment (one object each).
// There is no THREE.Scene behind it and no scene model on this side either: the reader reports the
// model's objects and which one each segment draws, per-object state is segment state in the renderer,
// and input is mapped to the camera's semantic operations (orbit, pan, zoom, fit, look from).
import type {ResultMeshData, ResultMeshLayer} from "@/viewport/api/resultLayer";
import {NativeResultLayer} from "./nativeResultLayer";
import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {dragAction, mouseButtonOf, wheelZoomFactor, type CameraAction} from "@/utils/camera/cameraMapping";
import {startRenderer} from "@/viewport/adacpp/rendererModule";
import {InstanceFlag, SegmentFlag, Topology, type AdacppModule, type CameraPoseInfo, type GpuDeviceLike, type LoadedGlbObjects, type RawPick, type WebRendererApi} from "@/viewport/adacpp/types";

/** The viewer's background (#393939) and selection colour, as the three.js viewport has them. */
const BACKGROUND = 0x39 / 255;
const SELECTED = (0 | (0 << 8) | (255 << 16) | (255 << 24)) >>> 0;
const FEATURE_EDGE_ANGLE_DEG = 30;
const EDGE_COLOR: [number, number, number, number] = [0, 0, 0, 1];
/** A press released within this many pixels of where it went down is a click, not a drag. */
const TAP_SLOP_PX = 6;
const MAX_PIXEL_RATIO = 2;
const HOME_AZIMUTH_DEG = -135;
const HOME_ELEVATION_DEG = 30;
/** Fit and the named views move there over this long. */
const MOVE_SECONDS = 0.25;
/** The share of a drag's speed kept per 1/60 s once released, when the user's mapping asks for a glide. */
const GLIDE_INERTIA = 0.8;
/** A frame after an idle spell must not advance a glide or a move by the whole spell. */
const MAX_FRAME_SECONDS = 0.05;

/** As many section planes as the renderer takes. */
const MAX_CLIP_PLANES = 6;

type Bounds = [number, number, number, number, number, number];

export interface NativeObject {
    id: string;
    /** Its names from the root down; the last is its own. */
    path: string[];
}

/** A set of ids that remembers whether it has changed since last asked. */
class TrackedSet extends Set<string> {
    changed = false;
    override add(id: string): this {
        if (!this.has(id)) this.changed = true;
        return super.add(id);
    }
    override delete(id: string): boolean {
        const had = super.delete(id);
        if (had) this.changed = true;
        return had;
    }
    override clear(): void {
        if (this.size) this.changed = true;
        super.clear();
    }
}

let canvasSerial = 0;

/** The box (min xyz, max xyz) around `positions` (xyz per vertex) placed by `matrix` (column-major
 *  4x4, identity when absent); null for none. */
function boundsOf(positions: Float32Array, matrix?: ArrayLike<number> | null): Bounds | null {
    if (positions.length < 3) return null;
    const m = matrix ?? [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    const box: Bounds = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
    for (let i = 0; i + 2 < positions.length; i += 3) {
        const x = positions[i], y = positions[i + 1], z = positions[i + 2];
        for (let k = 0; k < 3; k++) {
            const w = m[k] * x + m[4 + k] * y + m[8 + k] * z + m[12 + k];
            if (w < box[k]) box[k] = w;
            if (w > box[k + 3]) box[k + 3] = w;
        }
    }
    return box;
}

export class NativeScene {
    /** Per instance, the object index of each of its segments (-1: none). */
    private segments = new Map<number, Int32Array>();
    private table: LoadedGlbObjects | null = null;
    private indexOfId = new Map<string, number>();
    private bounds: Bounds | null = null;
    private lastFrame = 0;
    private objectList: NativeObject[] = [];
    private readonly selected = new Set<string>();
    private readonly hidden = new TrackedSet();
    private dirty = true;
    private raf = 0;
    private disposed = false;
    private readonly cleanups: (() => void)[] = [];
    /** Selection, visibility or the object list changed. */
    onChange: (() => void) | null = null;
    /** A click picked an object (its id), or empty space (null); the selection is already updated. */
    onPick: ((id: string | null) => void) | null = null;
    /** The key the loaded model is known by outside the viewport. */
    modelKey = "";
    /** The loaded GLB's scene extras, as JSON text. */
    sceneExtras = "";
    /** A different object whenever the selection has changed. */
    selectionVersion: object = {};
    /** Called after each change of the selection (or of what is hidden). */
    readonly onSelection = new Set<() => void>();
    /** Called after each frame drawn: what follows the camera on screen (the orientation gizmo) redraws. */
    readonly onFrame = new Set<() => void>();
    /** Called after a model replaced the scene: helper lines are gone with it, and the box has changed. */
    readonly onLoaded = new Set<() => void>();
    /** Asked at each press before the camera: return a handler to take the drag (it gets every move
     *  until release), or null to leave it to the camera. */
    dragInterceptor: ((e: PointerEvent) => ((e: PointerEvent) => void) | null) | null = null;
    /** Helper lines by handle (their instance): the geometry to free with each. */
    private readonly helperLines = new Map<number, number>();
    /** The renderer stopped (device lost, a failed frame): the caller falls back. */
    onFailure: ((reason: string) => void) | null = null;
    /** Why it stopped, kept for a caller that attaches `onFailure` after the fact: a device can be lost
     *  before the first frame. */
    failure: string | null = null;
    /** The result meshes drawn now (createResultLayer). */
    private readonly resultLayers = new Set<NativeResultLayer>();
    /** Called before each frame is drawn, with the seconds since the last one (0 after an idle
     *  spell). Returning true asks for another frame: what plays a result keeps the loop running. */
    beforeFrame: ((dtSeconds: number) => boolean) | null = null;

    private constructor(
        private readonly container: HTMLElement,
        private readonly input: HTMLElement,
        private readonly canvas: HTMLCanvasElement,
        private readonly module: AdacppModule,
        private readonly renderer: WebRendererApi,
        private readonly device: GpuDeviceLike,
    ) {
        renderer.setBackground(BACKGROUND, BACKGROUND, BACKGROUND);
        renderer.setCameraControlled(true);
        const glide = () => renderer.cameraSetDamping(useCameraControlsStore.getState().mapping.damping ? GLIDE_INERTIA : 0);
        glide();
        this.cleanups.push(useCameraControlsStore.subscribe(glide));
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
        if (typeof renderer.setCameraControlled !== "function") {
            renderer.delete();
            canvas.remove();
            throw new Error("the WebGPU renderer module is older than this viewer (no camera of its own)");
        }
        return new NativeScene(container, input, canvas, module, renderer, device);
    }

    /** Replace the model with a GLB, known to the rest of the viewer as `modelKey`. Throws when it has
     *  nothing to draw. */
    load(bytes: Uint8Array, modelKey = ""): void {
        this.modelKey = modelKey;
        this.sceneExtras = "";
        this.detachResultLayers();
        this.renderer.clear();
        this.sceneEpoch++;
        this.selected.clear();
        this.hidden.clear();
        const p = this.module._malloc(bytes.byteLength);
        this.module.HEAPU8.set(bytes, p);
        let loaded;
        try {
            loaded = this.renderer.loadGlb(p, bytes.byteLength, {featureEdgeAngleDeg: FEATURE_EDGE_ANGLE_DEG, lineColor: EDGE_COLOR, sectionCaps: true});
        } finally {
            this.module._free(p);
        }
        if (!loaded.geometries.length) throw new Error("the model has nothing to draw");
        const table = loaded.objects;
        this.table = table;
        this.sceneExtras = loaded.extras;
        this.segments = new Map(loaded.geometries.map((g) => [g.instance, g.segmentObjects]));
        this.indexOfId = new Map(table.ids.map((id, i) => [id, i]));
        // The list is of what can be picked: the objects that draw something themselves.
        const list: NativeObject[] = [];
        for (let i = 0; i < table.count; i++) {
            if (table.segmentOffsets[i + 1] === table.segmentOffsets[i]) continue;
            const path: string[] = [];
            for (let o = i; o >= 0; o = table.parents[o]) path.unshift(table.names[o] || table.ids[o]);
            list.push({id: table.ids[i], path});
        }
        this.objectList = list.sort((x, y) => x.path.join("/").localeCompare(y.path.join("/")));
        this.bounds = loaded.bounds;
        this.helperLines.clear(); // renderer.clear() took them
        this.home(0);
        for (const listener of this.onLoaded) listener();
        this.onChange?.();
    }

    /** Draw a result mesh from typed arrays: its colours, displacement and deformation scale are
     *  then set through the layer returned. With `replaceModel` the loaded model goes and the mesh
     *  is what the viewport shows and frames. */
    createResultLayer(data: ResultMeshData, opts: {replaceModel?: boolean} = {}): ResultMeshLayer {
        if (opts.replaceModel) {
            this.detachResultLayers();
            this.renderer.clear();
            this.modelKey = "";
            this.sceneExtras = "";
            this.table = null;
            this.segments = new Map();
            this.indexOfId = new Map();
            this.objectList = [];
            this.selected.clear();
            this.hidden.clear();
            this.selectionVersion = {};
            // As a model load: helper lines and sprites went with renderer.clear().
            this.helperLines.clear();
            this.sceneEpoch++;
        }
        const layer = new NativeResultLayer(this.module, this.renderer, data, () => this.invalidate());
        this.resultLayers.add(layer);
        if (opts.replaceModel) {
            this.bounds = boundsOf(data.positions, data.matrix);
            this.home(0);
            for (const listener of this.onLoaded) listener();
            for (const listener of this.onSelection) listener();
            this.onChange?.();
        }
        this.invalidate();
        return layer;
    }

    /** The renderer is about to drop everything it draws: the result layers go with it. */
    private detachResultLayers(): void {
        for (const layer of this.resultLayers) layer.detach();
        this.resultLayers.clear();
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

    get selectedIds(): string[] {
        return [...this.selected];
    }

    /** Make `ids` the selection, or with `add` add them to it; ids that draw nothing themselves, or are
     *  hidden, are left out. Returns how many of `ids` it selected. */
    setSelection(ids: readonly string[], add = false): number {
        if (!add) this.selected.clear();
        let taken = 0;
        for (const id of ids) {
            if (!this.draws(id) || this.hidden.has(id)) continue;
            this.selected.add(id);
            taken++;
        }
        this.pushStates();
        return taken;
    }

    get selectionSize(): number {
        return this.selected.size;
    }

    /** Draw line segments (xyz per point, two points per segment) over the model, in one linear colour:
     *  a helper, never picked. Returns the handle for `removeLines`. */
    addLines(points: Float32Array, color: readonly [number, number, number], widthPx: number): number {
        const M = this.module;
        const count = points.length / 3;
        const indices = Uint32Array.from({length: count}, (_, i) => i);
        const pPos = M._malloc(points.byteLength), pIdx = M._malloc(indices.byteLength), pMat = M._malloc(64);
        M.HEAPF32.set(points, pPos >> 2);
        M.HEAPU32.set(indices, pIdx >> 2);
        M.HEAPF32.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], pMat >> 2);
        try {
            const geometry = this.renderer.addGeometry(Topology.Lines, pPos, count, 0, 0, 0, pIdx, count, 0, 0, 0);
            const instance = this.renderer.addInstance(geometry);
            const flags = InstanceFlag.Unlit | InstanceFlag.NoPick | InstanceFlag.OnTop;
            this.renderer.setInstance(instance, pMat, color[0], color[1], color[2], 1, flags, 0, 0, 1, false, widthPx * this.pixelRatio(), 0, 0);
            this.helperLines.set(instance, geometry);
            this.invalidate();
            return instance;
        } finally {
            M._free(pPos);
            M._free(pIdx);
            M._free(pMat);
        }
    }

    removeLines(handle: number): void {
        const geometry = this.helperLines.get(handle);
        if (geometry === undefined) return;
        this.helperLines.delete(handle);
        this.renderer.removeInstance(handle);
        this.renderer.removeGeometry(geometry);
        this.invalidate();
    }

    /** Where a world point is on screen, in client pixels; null behind the camera. */
    worldToClient(p: readonly [number, number, number]): {x: number; y: number} | null {
        this.syncSize();
        const {view: v, proj: q} = this.renderer.cameraMatrices();
        const e = [0, 1, 2, 3].map((r) => v[r] * p[0] + v[4 + r] * p[1] + v[8 + r] * p[2] + v[12 + r]);
        const c = [0, 1, 2, 3].map((r) => q[r] * e[0] + q[4 + r] * e[1] + q[8 + r] * e[2] + q[12 + r] * e[3]);
        if (c[3] <= 1e-9) return null;
        const rect = this.canvas.getBoundingClientRect();
        return {x: rect.left + (c[0] / c[3] * 0.5 + 0.5) * rect.width, y: rect.top + (0.5 - c[1] / c[3] * 0.5) * rect.height};
    }

    /** The view as it is now, drawn at `width` x `height` pixels onto a canvas of its own; null when the
     *  picture cannot be read back. The view on screen is redrawn at its own size afterwards. */
    capture(width: number, height: number): HTMLCanvasElement | null {
        const out = document.createElement("canvas");
        out.width = width;
        out.height = height;
        const ctx = out.getContext("2d");
        if (!ctx || this.disposed) return null;
        try {
            this.canvas.width = width;
            this.canvas.height = height;
            this.renderer.resize(width, height);
            this.renderer.render();
            // The frame just drawn is the canvas' picture until this task ends.
            ctx.drawImage(this.canvas, 0, 0);
        } catch (err) {
            console.warn("[adacpp-native] capture failed:", err);
            return null;
        } finally {
            this.invalidate();
        }
        return out;
    }

    /** The camera's axes in the world: `right` and `up` of the screen, and `back`, from the model towards
     *  the camera. */
    cameraAxes(): {right: [number, number, number]; up: [number, number, number]; back: [number, number, number]} {
        const {eye, target, up} = this.renderer.cameraPose();
        const norm = (v: number[]): [number, number, number] => {
            const l = Math.hypot(v[0], v[1], v[2]) || 1;
            return [v[0] / l, v[1] / l, v[2] / l];
        };
        const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
        const back = norm([eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]]);
        const right = norm(cross(up, back));
        return {right, up: norm(cross(back, right)), back};
    }

    /** The loaded model's box (min xyz, max xyz). */
    get modelBounds(): Bounds | null {
        return this.bounds;
    }

    /** Cut the model by planes (`normal . x + constant = 0`, the +normal side is kept); none removes the cut. */
    setSectionPlanes(
        planes: readonly {normal: readonly [number, number, number]; constant: number}[],
        capColor: readonly [number, number, number] | null = null,
    ): void {
        // The cut through closed solids is filled, in one colour (linear rgb) when given.
        this.renderer.setSectionCaps(planes.length > 0);
        if (capColor) this.renderer.setSectionCapColor(capColor[0], capColor[1], capColor[2], 1);
        const used = planes.slice(0, MAX_CLIP_PLANES);
        const p = this.module._malloc(Math.max(1, used.length) * 16);
        used.forEach((plane, i) => this.module.HEAPF32.set([...plane.normal, plane.constant], (p >> 2) + i * 4));
        try {
            this.renderer.setClipPlanes(p, used.length);
        } finally {
            this.module._free(p);
        }
        this.invalidate();
    }

    /** Frame the whole model. */
    fit(seconds = 0): void {
        if (!this.bounds) return;
        this.frame(this.bounds, seconds);
    }

    /** The whole model from the starting direction: what a newly loaded model shows. */
    home(seconds = MOVE_SECONDS): void {
        if (!this.bounds) return;
        this.renderer.cameraSetOrientation(HOME_AZIMUTH_DEG, HOME_ELEVATION_DEG, 0, seconds);
        this.frame(this.bounds, seconds);
    }

    /** Frame the selected objects; false when none of them is drawn. */
    fitSelection(seconds = MOVE_SECONDS): boolean {
        const table = this.table;
        if (!table) return false;
        const box: Bounds = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
        for (const id of this.selected) {
            const i = this.indexOfId.get(id);
            if (i === undefined || table.bounds[i * 6] > table.bounds[i * 6 + 3]) continue;
            for (let k = 0; k < 3; k++) {
                box[k] = Math.min(box[k], table.bounds[i * 6 + k]);
                box[k + 3] = Math.max(box[k + 3], table.bounds[i * 6 + k + 3]);
            }
        }
        if (box[0] > box[3]) return false;
        this.frame(box, seconds);
        return true;
    }

    private frame(b: Bounds, seconds: number): void {
        this.syncSize();
        this.renderer.cameraFit(b[0], b[1], b[2], b[3], b[4], b[5], 0, seconds);
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

    /** Whether `id` is an object that draws something itself. */
    draws(id: string): boolean {
        const i = this.indexOfId.get(id);
        return i !== undefined && !!this.table && this.table.segmentOffsets[i + 1] > this.table.segmentOffsets[i];
    }

    /** Hide or show `ids`. Hiding clears the selection, whose highlight would otherwise keep drawing. */
    setHiddenMany(ids: readonly string[], hidden: boolean): void {
        for (const id of ids) {
            if (!this.draws(id)) continue;
            if (hidden) this.hidden.add(id);
            else this.hidden.delete(id);
        }
        if (hidden) this.selected.clear();
        this.pushStates();
    }

    /** Show `ids` and hide every other object. */
    isolate(ids: readonly string[]): void {
        const keep = new Set(ids);
        this.hidden.clear();
        for (const o of this.objectList) if (!keep.has(o.id)) this.hidden.add(o.id);
        this.pushStates();
    }

    /** Hide every object that is not selected. */
    hideOthers(): void {
        for (const o of this.objectList) if (!this.selected.has(o.id)) this.hidden.add(o.id);
        this.pushStates();
    }

    /** Look at the whole model from `direction` (the unit vector from the model to the camera, z up). */
    viewFrom(direction: readonly [number, number, number], seconds = MOVE_SECONDS): void {
        if (!this.bounds) return;
        const deg = 180 / Math.PI;
        const azimuth = Math.atan2(direction[0], direction[1]) * deg;
        const elevation = Math.asin(Math.max(-1, Math.min(1, direction[2]))) * deg;
        this.renderer.cameraSetOrientation(azimuth, elevation, 0, seconds);
        this.frame(this.bounds, seconds);
    }

    /** Orbit by the given angles (radians), as a key press does. */
    orbitBy(azimuth: number, elevation: number): void {
        this.syncSize();
        // The camera takes a drag in pixels, half a turn per viewport height.
        const h = this.canvas.height;
        this.renderer.cameraOrbit((azimuth / Math.PI) * h, (elevation / Math.PI) * h);
        this.invalidate();
    }

    /** Pan by fractions of the viewport's height, as a key press does. */
    panBy(right: number, up: number): void {
        this.syncSize();
        const h = this.canvas.height;
        this.renderer.cameraPan(-right * h, up * h);
        this.invalidate();
    }

    /** Every segment's state from the two sets: hidden wins over selected. */
    private pushStates(): void {
        const ids = this.table?.ids ?? [];
        for (const [instance, segments] of this.segments) {
            const states = new Uint32Array(segments.length * 2);
            segments.forEach((object, i) => {
                if (object < 0) return;
                const id = ids[object];
                if (this.hidden.has(id)) states[i * 2 + 1] = SegmentFlag.Hidden;
                else if (this.selected.has(id)) {
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
        this.selectionVersion = {};
        for (const listener of this.onSelection) listener();
        // What draws visibility (the Scene tree's rows) re-reads on this; a selection alone is not one.
        if (this.hidden.changed) {
            this.hidden.changed = false;
            useVisibilityStore.getState().bump();
        }
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
        const object = raw ? (this.segments.get(raw.instance)?.[raw.segment] ?? -1) : -1;
        return object >= 0 ? (this.table?.ids[object] ?? null) : null;
    }

    invalidate(): void {
        this.dirty = true;
        if (!this.raf && !this.disposed) this.raf = requestAnimationFrame(() => this.tick());
    }

    /** CSS pixels to the drawing buffer's, which is what the camera and pick count in. */
    private pixelRatio(): number {
        return Math.min(globalThis.devicePixelRatio || 1, MAX_PIXEL_RATIO);
    }

    /** The drawing buffer at the container's size; the renderer's camera takes its viewport from it. */
    private syncSize(): void {
        const ratio = this.pixelRatio();
        const w = Math.max(1, Math.floor(this.container.clientWidth * ratio));
        const h = Math.max(1, Math.floor(this.container.clientHeight * ratio));
        if (this.canvas.width !== w || this.canvas.height !== h) {
            this.canvas.width = w;
            this.canvas.height = h;
        }
        this.renderer.resize(w, h);
    }

    private tick(): void {
        this.raf = 0;
        if (this.disposed || !this.dirty) return;
        this.dirty = false;
        const now = performance.now();
        const dt = this.lastFrame ? Math.min((now - this.lastFrame) / 1000, MAX_FRAME_SECONDS) : 0;
        this.lastFrame = now;
        try {
            this.syncSize();
            // A move to a view, or the glide after a drag, runs for as long as the camera says it does.
            const moving = this.renderer.cameraUpdate(dt);
            const playing = this.beforeFrame?.(dt) ?? false;
            this.renderer.render();
            for (const listener of this.onFrame) listener();
            if (moving) this.invalidate();
            else if (playing) this.invalidate();
            else this.lastFrame = 0;
        } catch (err) {
            this.fail(err instanceof Error ? err.message : String(err));
        }
    }

    private fail(reason: string): void {
        if (this.failure) return;
        this.failure = reason;
        this.onFailure?.(reason);
    }

    /** Drags, the wheel and touch gestures move the camera as the user's mapping says
     *  (state/cameraControlsStore); a click selects. */
    private bindInput(): void {
        const el = this.input;
        const pointers = new Map<number, {x: number; y: number}>();
        let lastPinch = 0;
        let tap: {id: number; x: number; y: number} | null = null;
        /** What the drag in progress does: decided at the press, from its button and modifiers. */
        let action: CameraAction = "none";
        /** A drag something else took from the camera at its press (dragInterceptor). */
        let taken: ((e: PointerEvent) => void) | null = null;
        const mapping = () => useCameraControlsStore.getState().mapping;
        const camera = this.renderer;
        /** A drag of (dx, dy) CSS pixels. */
        const move = (kind: CameraAction, dx: number, dy: number) => {
            const speed = mapping().orbitSpeed;
            const px = this.pixelRatio();
            if (kind === "orbit") camera.cameraOrbit(dx * speed * px, dy * speed * px);
            else if (kind === "pan") camera.cameraPan(dx * px, dy * px);
            else if (kind === "zoom") camera.cameraZoom(Math.exp(dy * 0.005 * mapping().zoomSpeed));
        };
        const on = <K extends keyof HTMLElementEventMap>(type: K, fn: (e: HTMLElementEventMap[K]) => void, opts?: AddEventListenerOptions) => {
            el.addEventListener(type, fn as EventListener, opts);
            this.cleanups.push(() => el.removeEventListener(type, fn as EventListener, opts));
        };
        on("pointerdown", (e) => {
            el.setPointerCapture(e.pointerId);
            taken = this.dragInterceptor?.(e) ?? null;
            if (taken) return;
            pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
            lastPinch = 0;
            tap = pointers.size === 1 && e.button === 0 ? {id: e.pointerId, x: e.clientX, y: e.clientY} : null;
            const button = e.pointerType === "mouse" ? mouseButtonOf(e.button) : null;
            action = button ? dragAction(mapping(), button, e) : mapping().touch.one;
        });
        on("pointermove", (e) => {
            if (taken) return taken(e);
            const prev = pointers.get(e.pointerId);
            if (!prev) return;
            const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
            if (tap && Math.hypot(e.clientX - tap.x, e.clientY - tap.y) > TAP_SLOP_PX) tap = null;
            pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
            if (pointers.size >= 2) {
                const two = mapping().touch.two;
                if (two === "none") return;
                const [a, b] = [...pointers.values()];
                const d = Math.hypot(a.x - b.x, a.y - b.y);
                if (lastPinch > 0 && d > 0) camera.cameraZoom(lastPinch / d);
                lastPinch = d;
                // Each finger reports its own move: half of each makes the pair's.
                move(two === "zoom-orbit" ? "orbit" : "pan", dx / 2, dy / 2);
            } else {
                move(action, dx, dy);
            }
            this.invalidate();
        });
        on("pointerup", (e) => {
            if (taken) return void (taken = null);
            const t = tap;
            tap = null;
            pointers.delete(e.pointerId);
            lastPinch = 0;
            if (t && t.id === e.pointerId) {
                const additive = e.ctrlKey || e.metaKey || e.shiftKey;
                void this.pick(e.clientX, e.clientY).then((id) => {
                    this.select(id, additive && !!id);
                    this.onPick?.(id);
                });
            }
        });
        on("pointercancel", (e) => {
            taken = null;
            tap = null;
            pointers.delete(e.pointerId);
            lastPinch = 0;
        });
        on("contextmenu", (e) => e.preventDefault());
        on("wheel", (e) => {
            e.preventDefault();
            const factor = wheelZoomFactor(mapping(), e.deltaY);
            if (factor === null) return;
            if (mapping().wheel.toCursor) {
                const rect = this.canvas.getBoundingClientRect();
                const px = this.pixelRatio();
                camera.cameraZoomAt(factor, (e.clientX - rect.left) * px, (e.clientY - rect.top) * px);
            } else {
                camera.cameraZoom(factor);
            }
            this.invalidate();
        }, {passive: false});
    }

    // Overlay images: what viewport/native/nativeBillboards.ts draws labels, markers and icons with.

    /** Goes up each time the renderer's scene is emptied (a load), which takes every sprite and
     *  texture made before with it. */
    sceneEpoch = 0;

    /** Whether the renderer module draws sprites at all (an older one does not). */
    get drawsSprites(): boolean {
        return typeof this.renderer.addSprite === "function" && typeof this.renderer.addTexture === "function";
    }

    /** The element the view fills, and the one its clicks arrive on. */
    get elements(): {view: HTMLElement; input: HTMLElement} {
        return {view: this.container, input: this.input};
    }

    get cameraPose(): CameraPoseInfo {
        return this.renderer.cameraPose();
    }

    /** A texture from RGBA pixels, top row first with straight alpha, as `getImageData().data`. */
    addTexture(rgba: Uint8Array | Uint8ClampedArray, width: number, height: number): number {
        const p = this.module._malloc(rgba.length);
        this.module.HEAPU8.set(rgba, p);
        try {
            return this.renderer.addTexture(p, width, height);
        } finally {
            this.module._free(p);
        }
    }

    removeTexture(texture: number): void {
        if (this.disposed) return;
        this.renderer.removeTexture(texture);
    }

    addSprite(): number {
        return this.renderer.addSprite();
    }

    /** Place a sprite: its anchor in the world, its scale and centre as three.js' Sprite has them,
     *  SpriteFlag bits and the order it is drawn in. Untinted and unrotated. */
    setSprite(
        sprite: number, texture: number, position: readonly [number, number, number], scaleX: number, scaleY: number,
        centerX: number, centerY: number, flags: number, order: number,
    ): void {
        if (this.disposed) return;
        this.renderer.setSprite(sprite, texture, position[0], position[1], position[2], scaleX, scaleY, centerX, centerY, 0, 1, 1, 1, 1, flags, order);
        this.invalidate();
    }

    removeSprite(sprite: number): void {
        if (this.disposed) return;
        this.renderer.removeSprite(sprite);
        this.invalidate();
    }

    /** The middle of the boxes of `ids`, averaged; null when none of them draws anything. */
    objectsCentre(ids: readonly string[]): [number, number, number] | null {
        const table = this.table;
        if (!table) return null;
        const sum: [number, number, number] = [0, 0, 0];
        let n = 0;
        for (const id of ids) {
            const i = this.indexOfId.get(id);
            if (i === undefined || table.bounds[i * 6] > table.bounds[i * 6 + 3]) continue;
            for (let k = 0; k < 3; k++) sum[k] += (table.bounds[i * 6 + k] + table.bounds[i * 6 + k + 3]) / 2;
            n++;
        }
        return n ? [sum[0] / n, sum[1] / n, sum[2] / n] : null;
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        if (this.raf) cancelAnimationFrame(this.raf);
        for (const c of this.cleanups) c();
        this.detachResultLayers();
        try {
            this.renderer.delete();
            this.device.destroy();
        } catch (err) {
            console.warn("[adacpp-native] dispose:", err);
        }
        this.canvas.remove();
    }
}
