// Layers a plugin draws whose data changes over time: lines, points and images lying in the world,
// each a handle whose data is replaced in place, and the plugin's side of the playback clock and of
// the Layers panel. Beside sceneOverlays.ts, whose lines and labels are drawn once and follow an FEA
// result.
//
// A plugin states what to draw in plain data, in the scene's own coordinates (those of the loaded
// model's file, z-up); core draws it through the viewport on screen (viewport/api/overlayLayer.ts,
// imageLayer.ts) and places it where that viewport places the model. Colours are sRGB, as CSS.
// Everything a plugin made here goes with `releaseOwnerLayers(owner)`.
//
// No three.js in this module or in anything it imports.

import {joinLayer, listLayers, setLayerOpacity, setLayerVisible, subscribeLayers} from "@/state/layerRegistry";
import {playbackClock} from "@/state/playbackClock";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {linearChannel, linearColor} from "@/utils/scene/sceneColor";
import type {ImageLayer} from "@/viewport/api/imageLayer";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {activeViewport, type ViewportApi} from "@/viewport/api/viewportApi";
import type {
    PluginLayers,
    PluginPlayback,
    SceneImage,
    SceneImageSpec,
    SceneLayerLook,
    SceneLayerRef,
    SceneLineLayer,
    SceneLineLayerSpec,
    ScenePointLayer,
    ScenePointLayerSpec,
} from "./registry";

// ----- what an owner has made -----------------------------------------------

/** How to take away each thing an owner made, until it is taken away. */
const owned = new Map<string, Set<() => void>>();

function own(owner: string, release: () => void): () => void {
    let set = owned.get(owner);
    if (!set) owned.set(owner, (set = new Set()));
    const held = set;
    const once = (): void => {
        if (!held.delete(once)) return;
        if (held.size === 0 && owned.get(owner) === held) owned.delete(owner);
        release();
    };
    held.add(once);
    return once;
}

/** Remove every layer and playback track `owner` made (SceneHandle.remove(owner) reaches this
 *  through `removeOwnerOverlays`). */
export function releaseOwnerLayers(owner: string): void {
    for (const release of [...(owned.get(owner) ?? [])]) release();
}

// ----- where the scene's coordinates are in the viewport ---------------------------

/** What is drawn now, each as the function that moves it to where the viewport places the model now. */
const placed = new Set<() => void>();
let offFrame: (() => void) | null = null;

/** The scene's own coordinates in `viewport`'s world, as a matrix; null when they are the same.
 *  The three.js viewport recentres what it loads; the three.js-free one draws a model where its
 *  file puts it. */
function sceneMatrix(viewport: ViewportApi): number[] | null {
    const offset = viewport.kind === "three" ? useSceneFrameStore.getState().offset : null;
    if (!offset || (offset[0] === 0 && offset[1] === 0 && offset[2] === 0)) return null;
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, offset[0], offset[1], offset[2], 1];
}

function follow(viewport: ViewportApi, move: (matrix: number[] | null) => void): () => void {
    const held = (): void => move(sceneMatrix(viewport));
    placed.add(held);
    // A model loaded later is recentred then: what was drawn before it moves with it.
    offFrame ??= useSceneFrameStore.subscribe(() => placed.forEach((each) => each()));
    return () => {
        placed.delete(held);
        if (placed.size === 0) {
            offFrame?.();
            offFrame = null;
        }
    };
}

// ----- the layers a viewport draws an owner's things through ---------------------------

const layerName = (owner: string): string => `plugin:${owner}:layers`;
const rowId = (owner: string, id: string): string => `${owner}:${id}`;

/** How many things an owner draws through each of its viewport layers, so the layer can be
 *  released with the last. */
const inUse = new Map<OverlayLayer | ImageLayer, number>();

function retain<T extends OverlayLayer | ImageLayer>(layer: T): T {
    inUse.set(layer, (inUse.get(layer) ?? 0) + 1);
    return layer;
}

function letGo(layer: OverlayLayer | ImageLayer): void {
    const left = (inUse.get(layer) ?? 1) - 1;
    if (left > 0) return void inUse.set(layer, left);
    inUse.delete(layer);
    layer.dispose();
}

/** A drawn thing's look: the plugin's own, times its row's in the Layers panel. */
class Look {
    private visible: boolean;
    private opacity: number;
    private rowVisible = true;
    private rowOpacity = 1;
    private leave: (() => void) | null = null;

    constructor(
        spec: {visible?: boolean; opacity?: number},
        private readonly apply: (visible: boolean, opacity: number) => void,
    ) {
        this.visible = spec.visible !== false;
        this.opacity = spec.opacity ?? 1;
    }

    /** What it is drawn with to begin with, before a row has had its say. */
    get startVisible(): boolean {
        return this.visible;
    }

    get startOpacity(): number {
        return this.opacity;
    }

    join(owner: string, row: SceneLayerRef | undefined): void {
        if (!row) return;
        const apply = (visible: boolean, opacity: number): void => {
            this.rowVisible = visible;
            this.rowOpacity = opacity;
            this.push();
        };
        // How the row starts, unless the user left it otherwise.
        this.leave = joinLayer({id: rowId(owner, row.id), label: row.label, group: row.group}, apply, {visible: row.visible, opacity: row.opacity});
    }

    set(look: SceneLayerLook): void {
        if (look.visible !== undefined) this.visible = look.visible;
        if (look.opacity !== undefined) this.opacity = look.opacity;
        this.push();
    }

    private push(): void {
        this.apply(this.visible && this.rowVisible, this.opacity * this.rowOpacity);
    }

    release(): void {
        this.leave?.();
        this.leave = null;
    }
}

const asFloats = (values: Float32Array | readonly number[] | undefined): Float32Array =>
    values instanceof Float32Array ? values : Float32Array.from(values ?? []);

/** A buffer of at least `length` floats: `held` when it is large enough. */
function room(held: Float32Array, length: number): Float32Array {
    return held.length >= length ? held : new Float32Array(Math.ceil(length * 1.5));
}

/** `count` vertices of one open line as its segments (two ends each) into `out`. Returns how many. */
export function polylineSegments(points: ArrayLike<number>, count: number, out: Float32Array): number {
    const segments = Math.max(0, count - 1);
    for (let s = 0, p = 0, o = 0; s < segments; s++, p += 3, o += 6) {
        out[o] = points[p];
        out[o + 1] = points[p + 1];
        out[o + 2] = points[p + 2];
        out[o + 3] = points[p + 3];
        out[o + 4] = points[p + 4];
        out[o + 5] = points[p + 5];
    }
    return segments;
}

// ----- lines ------------------------------------------------------------------

const NO_LINES: SceneLineLayer = {setSegments: () => {}, setPolyline: () => {}, setLook: () => {}, remove: () => {}};

export function addLineLayer(owner: string, spec: SceneLineLayerSpec = {}): SceneLineLayer {
    const viewport = activeViewport();
    if (!viewport) return NO_LINES;
    let scratch: Float32Array = new Float32Array(0);
    let segments = asFloats(spec.segments);
    if (segments.length % 6 !== 0) throw new Error(`addLineLayer: segments needs 6 numbers per segment, got ${segments.length}`);
    if (spec.polyline?.length) {
        const points = asFloats(spec.polyline);
        if (points.length % 3 !== 0) throw new Error(`addLineLayer: polyline needs 3 numbers per vertex, got ${points.length}`);
        scratch = new Float32Array(Math.max(0, points.length / 3 - 1) * 6);
        polylineSegments(points, points.length / 3, scratch);
        segments = scratch;
    }
    const layer = retain(viewport.overlays(layerName(owner)));
    const look = new Look(spec, (visible, opacity) => layer.setLook(handle, {visible, opacity}));
    // A layer of no segments yet is made with one of no length: there is nothing to draw of it.
    const empty = segments.length === 0;
    const handle = layer.addLines({
        segments: empty ? new Float32Array(6) : segments,
        color: linearColor(spec.color ?? "#ffffff"),
        widthPx: spec.widthPx,
        opacity: look.startOpacity,
        onTop: !!spec.throughModel,
        clipped: true,
        matrix: sceneMatrix(viewport),
    });
    if (empty) layer.setGeometry(handle, segments, 0);
    if (!look.startVisible) layer.setLook(handle, {visible: false});
    look.join(owner, spec.layer);
    const unfollow = follow(viewport, (matrix) => layer.setLook(handle, {matrix}));
    const remove = own(owner, () => {
        unfollow();
        look.release();
        layer.remove(handle);
        letGo(layer);
    });
    return {
        setSegments: (next, count) => layer.setGeometry(handle, next, count),
        setPolyline: (points, count) => {
            const vertices = Math.max(0, Math.min(count ?? Infinity, Math.floor(points.length / 3)));
            scratch = room(scratch, Math.max(0, vertices - 1) * 6);
            layer.setGeometry(handle, scratch, polylineSegments(points, vertices, scratch));
        },
        setLook: (change) => {
            if (change.color !== undefined) layer.setLook(handle, {color: linearColor(change.color)});
            look.set(change);
        },
        remove,
    };
}

// ----- points -----------------------------------------------------------------

const NO_POINTS: ScenePointLayer = {setPoints: () => {}, setLook: () => {}, remove: () => {}};

export function addPointLayer(owner: string, spec: ScenePointLayerSpec = {}): ScenePointLayer {
    const viewport = activeViewport();
    if (!viewport) return NO_POINTS;
    const positions = asFloats(spec.positions);
    if (positions.length % 3 !== 0) throw new Error(`addPointLayer: positions needs 3 numbers per point, got ${positions.length}`);
    const colored = spec.colors !== undefined;
    if (colored && spec.colors!.length !== positions.length) {
        throw new Error(`addPointLayer: colors needs 3 numbers per point (${positions.length}), got ${spec.colors!.length}`);
    }
    // Colours come in sRGB and are drawn linear: converted into a buffer kept for the next ones.
    let tint: Float32Array = new Float32Array(colored ? Math.max(3, positions.length) : 0);
    const linearInto = (colors: ArrayLike<number>, numbers: number): void => {
        for (let i = 0; i < numbers; i++) tint[i] = linearChannel(colors[i]);
    };
    if (colored) linearInto(spec.colors!, positions.length);
    const layer = retain(viewport.overlays(layerName(owner)));
    const look = new Look(spec, (visible, opacity) => layer.setLook(handle, {visible, opacity}));
    const empty = positions.length === 0;
    const handle = layer.addPoints({
        positions: empty ? new Float32Array(3) : positions,
        colors: colored ? (empty ? new Float32Array(3) : tint.subarray(0, positions.length)) : undefined,
        color: colored ? undefined : linearColor(spec.color ?? "#ffffff"),
        sizePx: spec.sizePx,
        opacity: look.startOpacity,
        onTop: !!spec.throughModel,
        clipped: true,
        matrix: sceneMatrix(viewport),
    });
    if (empty) layer.setGeometry(handle, positions, 0);
    if (!look.startVisible) layer.setLook(handle, {visible: false});
    look.join(owner, spec.layer);
    const unfollow = follow(viewport, (matrix) => layer.setLook(handle, {matrix}));
    const remove = own(owner, () => {
        unfollow();
        look.release();
        layer.remove(handle);
        letGo(layer);
    });
    return {
        setPoints: (next, count, colors) => {
            const n = Math.max(0, Math.min(count ?? Infinity, Math.floor(next.length / 3)));
            if (colored && colors) {
                tint = room(tint, n * 3);
                linearInto(colors, Math.min(n * 3, colors.length));
                layer.setGeometry(handle, next, n, tint);
            } else {
                layer.setGeometry(handle, next, n);
            }
        },
        setLook: (change) => {
            if (change.color !== undefined && !colored) layer.setLook(handle, {color: linearColor(change.color)});
            look.set(change);
        },
        remove,
    };
}

// ----- images -----------------------------------------------------------------

const NO_IMAGE: SceneImage = {setPixels: () => {}, setRect: () => {}, setLook: () => {}, unavailable: () => null, remove: () => {}};

export function addImage(owner: string, image: SceneImageSpec): SceneImage {
    const viewport = activeViewport();
    if (!viewport) return NO_IMAGE;
    if (image.pixels.length < image.width * image.height * 4) {
        throw new Error(`addImage: pixels needs 4 bytes per pixel (${image.width * image.height * 4}), got ${image.pixels.length}`);
    }
    const layer = retain(viewport.images(layerName(owner)));
    const look = new Look(image, (visible, opacity) => layer.setLook(handle, {visible, opacity}));
    let width = image.width, height = image.height;
    const handle = layer.add({
        origin: image.origin,
        u: image.u,
        v: image.v,
        width,
        height,
        pixels: image.pixels,
        opacity: look.startOpacity,
        visible: look.startVisible,
        onTop: !!image.throughModel,
        nearest: !!image.nearest,
        clipped: true,
        matrix: sceneMatrix(viewport),
    });
    look.join(owner, image.layer);
    const unfollow = follow(viewport, (matrix) => layer.setLook(handle, {matrix}));
    const remove = own(owner, () => {
        unfollow();
        look.release();
        layer.remove(handle);
        letGo(layer);
    });
    return {
        setPixels: (pixels, w, h) => {
            width = w ?? width;
            height = h ?? height;
            layer.setPixels(handle, pixels, w, h);
        },
        setRect: (rect) => layer.setRect(handle, rect),
        setLook: (change) => look.set(change),
        unavailable: () => layer.unavailable(width, height),
        remove,
    };
}

/** Why the viewport on screen cannot draw an image of this size; null when it can (or there is none). */
export function imageUnavailable(width: number, height: number): string | null {
    const viewport = activeViewport();
    if (!viewport) return null;
    // Asked of a layer of the viewport's, made for the asking and released again.
    const probe = viewport.images("plugin:probe");
    const answer = probe.unavailable(width, height);
    probe.dispose();
    return answer;
}

// ----- the playback clock and the Layers panel, as one plugin sees them ---------------------

export function makePluginPlayback(owner: string): PluginPlayback {
    return {
        addTrack: (track) => {
            const handle = playbackClock.addTrack({id: `${owner}:${track.id}`, duration: track.duration, show: track.show, label: track.label});
            const remove = own(owner, handle.remove);
            return {setDuration: handle.setDuration, remove};
        },
        state: () => playbackClock.state(),
        time: () => playbackClock.time(),
        subscribe: playbackClock.subscribe,
        play: () => playbackClock.play(),
        pause: () => playbackClock.pause(),
        seek: (seconds) => playbackClock.seek(seconds),
        setSpeed: (speed) => playbackClock.setSpeed(speed),
        setLoop: (loop) => playbackClock.setLoop(loop),
        followEnd: (follow) => playbackClock.setFollowEnd(follow),
    };
}

export function makePluginLayers(owner: string): PluginLayers {
    const prefix = `${owner}:`;
    return {
        list: () =>
            listLayers()
                .filter((row) => row.id.startsWith(prefix))
                .map((row) => ({id: row.id.slice(prefix.length), label: row.label, group: row.group, visible: row.visible, opacity: row.opacity})),
        setVisible: (id, visible) => setLayerVisible(rowId(owner, id), visible),
        setOpacity: (id, opacity) => setLayerOpacity(rowId(owner, id), opacity),
        subscribe: subscribeLayers,
    };
}
