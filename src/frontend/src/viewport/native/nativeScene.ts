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

/** Between a model's key and an object's id in it, in the one string the scene knows an object by.
 *  Neither a key nor an id contains it. */
const KEY_SEPARATOR = "\u0000";

/** The scene's name for object `id` of model `model`. */
export function objectKey(model: string, id: string): string {
    return model + KEY_SEPARATOR + id;
}

/** The model and the id an object key was made from. */
export function splitObjectKey(key: string): {model: string; id: string} {
    const at = key.indexOf(KEY_SEPARATOR);
    return at < 0 ? {model: "", id: key} : {model: key.slice(0, at), id: key.slice(at + 1)};
}

/** One loaded GLB: what the reader said it holds, and what it added to the renderer. */
interface LoadedModel {
    key: string;
    sourceName: string;
    table: LoadedGlbObjects;
    /** Each object's key in the scene, by its index in `table`. */
    keys: string[];
    instances: number[];
    geometries: number[];
    bounds: Bounds;
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
    private segments = new Map<number, {model: LoadedModel; objects: Int32Array}>();
    /** The models on screen, by their key. */
    private readonly models = new Map<string, LoadedModel>();
    /** Every object by its key: the model that holds it and its index in that model's table. */
    private readonly where = new Map<string, {model: LoadedModel; index: number}>();
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

    /** Show a GLB, known to the rest of the viewer as `modelKey`: instead of what is on screen, or with
     *  `add` beside it (a model already loaded under that key is replaced). Objects are known by
     *  `objectKey(modelKey, id)`. Throws when the GLB has nothing to draw. */
    load(bytes: Uint8Array, modelKey = "", options: {add?: boolean; sourceName?: string} = {}): void {
        const add = !!options.add && this.models.size > 0;
        if (add) {
            this.dropModel(modelKey);
        } else {
            this.detachResultLayers();
            this.renderer.clear();
            this.sceneEpoch++;
            this.helperLines.clear(); // renderer.clear() took them
            this.models.clear();
            this.where.clear();
            this.segments = new Map();
            this.selected.clear();
            this.hidden.clear();
        }
        this.modelKey = modelKey;
        this.sceneExtras = "";
        const p = this.module._malloc(bytes.byteLength);
        this.module.HEAPU8.set(bytes, p);
        let loaded;
        try {
            loaded = this.renderer.loadGlb(p, bytes.byteLength, {featureEdgeAngleDeg: FEATURE_EDGE_ANGLE_DEG, lineColor: EDGE_COLOR, sectionCaps: true});
        } finally {
            this.module._free(p);
        }
        if (!loaded.geometries.length) {
            this.rebuild();
            throw new Error("the model has nothing to draw");
        }
        const table = loaded.objects;
        const model: LoadedModel = {
            key: modelKey,
            sourceName: options.sourceName ?? "",
            table,
            keys: table.ids.map((id) => objectKey(modelKey, id)),
            instances: loaded.geometries.map((g) => g.instance),
            geometries: loaded.geometries.map((g) => g.geometry),
            bounds: loaded.bounds,
        };
        this.models.set(modelKey, model);
        this.sceneExtras = loaded.extras;
        for (const g of loaded.geometries) this.segments.set(g.instance, {model, objects: g.segmentObjects});
        model.keys.forEach((key, index) => this.where.set(key, {model, index}));
        this.rebuild();
        // A model that joins others leaves the camera where the user has it.
        if (!add) this.home(0);
        for (const listener of this.onLoaded) listener();
        if (add) this.pushStates();
        else this.onChange?.();
    }

    /** Take the model loaded from `sourceName` off the screen; false when there is none. */
    unloadSource(sourceName: string): boolean {
        const model = [...this.models.values()].find((m) => m.sourceName === sourceName);
        if (!model) return false;
        this.dropModel(model.key);
        this.rebuild();
        for (const listener of this.onLoaded) listener();
        this.pushStates();
        return true;
    }

    /** Remove one model's geometry and everything known about its objects. */
    private dropModel(key: string): void {
        const model = this.models.get(key);
        if (!model) return;
        for (const instance of model.instances) {
            this.renderer.removeInstance(instance);
            this.segments.delete(instance);
        }
        for (const geometry of model.geometries) this.renderer.removeGeometry(geometry);
        for (const objectKeyOf of model.keys) {
            this.where.delete(objectKeyOf);
            this.selected.delete(objectKeyOf);
            this.hidden.delete(objectKeyOf);
        }
        this.models.delete(key);
    }

    /** The list of pickable objects and the box around everything, from the models on screen. */
    private rebuild(): void {
        const list: NativeObject[] = [];
        let box: Bounds | null = null;
        for (const model of this.models.values()) {
            const table = model.table;
            // The list is of what can be picked: the objects that draw something themselves.
            for (let i = 0; i < table.count; i++) {
                if (table.segmentOffsets[i + 1] === table.segmentOffsets[i]) continue;
                const path: string[] = [];
                for (let o = i; o >= 0; o = table.parents[o]) path.unshift(table.names[o] || table.ids[o]);
                list.push({id: model.keys[i], path});
            }
            const b = model.bounds;
            box = box ? [Math.min(box[0], b[0]), Math.min(box[1], b[1]), Math.min(box[2], b[2]), Math.max(box[3], b[3]), Math.max(box[4], b[4]), Math.max(box[5], b[5])] : [...b];
        }
        this.objectList = list.sort((x, y) => x.path.join("/").localeCompare(y.path.join("/")));
        this.bounds = box;
    }

    /** The box (min xyz, max xyz) of object `key`, with everything below it; null when nothing of it is drawn. */
    private boxOf(key: string): Bounds | null {
        const at = this.where.get(key);
        if (!at) return null;
        const b = at.model.table.bounds, i = at.index * 6;
        return b[i] > b[i + 3] ? null : [b[i], b[i + 1], b[i + 2], b[i + 3], b[i + 4], b[i + 5]];
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
            this.models.clear();
            this.where.clear();
            this.segments = new Map();
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

    /** Where the camera is and what it looks at; `up` is the world's up axis. */
    viewPose(): {eye: [number, number, number]; target: [number, number, number]; up: [number, number, number]; fovDeg: number} {
        const pose = this.renderer.cameraPose();
        return {eye: [...pose.eye], target: [...pose.target], up: [0, 0, 1], fovDeg: pose.fov};
    }

    /** Put the camera at `eye` looking at `target`, rolled by `roll` radians about that direction
     *  (the world's up turned about it). */
    setCameraPose(eye: readonly [number, number, number], target: readonly [number, number, number], roll: number): void {
        let up: [number, number, number] = [0, 0, 1];
        if (roll !== 0) {
            // Rodrigues: the up axis turned by `roll` about the unit view direction.
            const d = [target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]];
            const l = Math.hypot(d[0], d[1], d[2]) || 1;
            const k = [d[0] / l, d[1] / l, d[2] / l];
            const c = Math.cos(roll), s = Math.sin(roll), kz = k[2];
            up = [
                -k[1] * s + k[0] * kz * (1 - c),
                k[0] * s + k[1] * kz * (1 - c),
                c + kz * kz * (1 - c),
            ];
        }
        const pose = this.renderer.cameraPose();
        this.renderer.cameraSetPose(eye[0], eye[1], eye[2], target[0], target[1], target[2], up[0], up[1], up[2], pose.fov, pose.orthographic, 0, 0);
        this.invalidate();
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
        const box: Bounds = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
        for (const id of this.selected) {
            const own = this.boxOf(id);
            if (!own) continue;
            for (let k = 0; k < 3; k++) {
                box[k] = Math.min(box[k], own[k]);
                box[k + 3] = Math.max(box[k + 3], own[k + 3]);
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
        const at = this.where.get(id);
        return !!at && at.model.table.segmentOffsets[at.index + 1] > at.model.table.segmentOffsets[at.index];
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
        for (const [instance, {model, objects: segments}] of this.segments) {
            const ids = model.keys;
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
        const drawn = raw ? this.segments.get(raw.instance) : undefined;
        const object = drawn ? (drawn.objects[raw!.segment] ?? -1) : -1;
        return drawn && object >= 0 ? (drawn.model.keys[object] ?? null) : null;
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
        const sum: [number, number, number] = [0, 0, 0];
        let n = 0;
        for (const id of ids) {
            const box = this.boxOf(id);
            if (!box) continue;
            for (let k = 0; k < 3; k++) sum[k] += (box[k] + box[k + 3]) / 2;
            n++;
        }
        return n ? [sum[0] / n, sum[1] / n, sum[2] / n] : null;
    }

    // Box selection: what viewport/native/nativeBoxSelection.ts asks, decided on each object's box.

    /** The camera's projection x view matrix (column-major), for the view as it is now. */
    viewProjection(): Float64Array {
        this.syncSize();
        const {view: v, proj: q} = this.renderer.cameraMatrices();
        const out = new Float64Array(16);
        for (let c = 0; c < 4; c++) {
            for (let r = 0; r < 4; r++) out[c * 4 + r] = q[r] * v[c * 4] + q[4 + r] * v[c * 4 + 1] + q[8 + r] * v[c * 4 + 2] + q[12 + r] * v[c * 4 + 3];
        }
        return out;
    }

    /** The keys of the objects drawn now whose box `takes` accepts: it is given every model's boxes
     *  (six numbers per object) and an object's index in them. Left out before it is asked: an object
     *  that draws nothing itself, a hidden one, and one `skip` names (its box and the offset in it). */
    drawnObjectsWhere(takes: (bounds: Float32Array, index: number) => boolean, skip?: (bounds: Float32Array, offset: number) => boolean): string[] {
        const out: string[] = [];
        for (const model of this.models.values()) {
            const {bounds, segmentOffsets, count} = model.table;
            for (let i = 0; i < count; i++) {
                const o = i * 6;
                if (segmentOffsets[i + 1] === segmentOffsets[i] || bounds[o] > bounds[o + 3]) continue;
                if (this.hidden.has(model.keys[i]) || skip?.(bounds, o)) continue;
                if (takes(bounds, i)) out.push(model.keys[i]);
            }
        }
        return out;
    }

    /** Draw see-through faces (xyz per point, three indices per triangle) in one linear colour, seen
     *  from both sides and behind whatever is in front of them: a helper, never picked. Returns the
     *  handle, which `removeLines` takes as it takes a line helper's. */
    addFaces(points: Float32Array, indices: Uint32Array, color: readonly [number, number, number], opacity: number): number {
        const M = this.module;
        const pPos = M._malloc(points.byteLength), pIdx = M._malloc(indices.byteLength), pMat = M._malloc(64);
        M.HEAPF32.set(points, pPos >> 2);
        M.HEAPU32.set(indices, pIdx >> 2);
        M.HEAPF32.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], pMat >> 2);
        try {
            const geometry = this.renderer.addGeometry(Topology.Triangles, pPos, points.length / 3, 0, 0, 0, pIdx, indices.length, 0, 0, 0);
            const instance = this.renderer.addInstance(geometry);
            const flags = InstanceFlag.Unlit | InstanceFlag.NoPick | InstanceFlag.Transparent | InstanceFlag.DoubleSided;
            this.renderer.setInstance(instance, pMat, color[0], color[1], color[2], opacity, flags, 0, 0, 1, false, 1, 0, 0);
            this.helperLines.set(instance, geometry);
            this.invalidate();
            return instance;
        } finally {
            M._free(pPos);
            M._free(pIdx);
            M._free(pMat);
        }
    }

    /** The box (min xyz, max xyz) around the selected objects; null when none of them is drawn. */
    selectionBounds(): Bounds | null {
        const box: Bounds = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
        for (const id of this.selected) {
            const own = this.boxOf(id);
            if (!own) continue;
            for (let k = 0; k < 3; k++) {
                box[k] = Math.min(box[k], own[k]);
                box[k + 3] = Math.max(box[k + 3], own[k + 3]);
            }
        }
        return box[0] > box[3] ? null : box;
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
