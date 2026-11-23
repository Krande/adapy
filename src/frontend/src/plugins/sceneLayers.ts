// Layers a plugin draws whose data changes over time: lines, points and images lying in the world,
// each a handle whose data is replaced in place, and the plugin's side of the playback clock and of
// the Layers panel. Beside sceneOverlays.ts, whose lines and labels are drawn once and follow an FEA
// result.
//
// A plugin states what to draw in plain data, in the scene's own coordinates (those of the loaded
// model's file, z-up); core draws it through the viewport on screen (viewport/api/overlayLayer.ts,
// imageLayer.ts) and places it where that viewport places the model. Colours are sRGB, as CSS.
//
// Each line, point and image layer is an entry of the overlay manager (viewport/api/overlayManager.ts):
// a clear of the scene takes it (a replace leaves it), and a viewport that takes the place of the one
// it was drawn on draws it again, with its data as last set. Everything a plugin made here goes with
// `releaseOwnerLayers(owner)` and `removeOwner(owner)` (`scene.remove(owner)` calls both).
//
// No three.js in this module or in anything it imports.

import {captureFrame} from "@/components/playback/frameCapture";
import {joinLayer, listLayers, setLayerOpacity, setLayerVisible, subscribeLayers} from "@/state/layerRegistry";
import {playbackClock} from "@/state/playbackClock";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {linearChannel, linearColor} from "@/utils/scene/sceneColor";
import type {ImageLayer, ImageRect} from "@/viewport/api/imageLayer";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {register, type OverlayHandle} from "@/viewport/api/overlayManager";
import {activeViewport} from "@/viewport/api/viewportApi";
import {createModelPose} from "@/viewport/api/modelPose";
import type {
    PluginLayers,
    PluginPlayback,
    SceneImage,
    SceneImageSpec,
    SceneLayerLook,
    SceneLayerRef,
    SceneLineLayer,
    SceneLineLayerSpec,
    SceneModelPose,
    ScenePointLayer,
    ScenePointLayerSpec,
} from "./registry";

// ----- what an owner has made -----------------------------------------------

/** How to take away each thing an owner made, until it is taken away. */
const owned = new Map<string, Set<() => void>>();

/** Keep `release` as something `owner` made: the returned function takes it away, once, and so does
 *  `releaseOwnerLayers(owner)`. What else a plugin puts in the scene is kept here too (./sceneBox.ts). */
export function own(owner: string, release: () => void): () => void {
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

/** Remove every playback track, posed model and placed box `owner` made (SceneHandle.remove(owner)
 *  reaches this through `removeOwnerOverlays`, which removes its drawings from the manager). */
export function releaseOwnerLayers(owner: string): void {
    for (const release of [...(owned.get(owner) ?? [])]) release();
}

/** The manager's tag for what a plugin draws over the scene (its lines, points, images, labels),
 *  apart from its highlights: `scene.remove(owner)` takes both, each by its own verb. */
export const DRAWING_KIND = "drawing";

/** A line, point or image layer a plugin drew, as an entry of the overlay manager: the `scene`
 *  lifetime (a clear removes it, a replace leaves it), drawn again on each viewport that comes on
 *  screen. Once removed -- by the handle's `remove`, `scene.remove(owner)` or a clear -- the handle's
 *  `alive` is false, and the layer handle's calls do nothing. */
function drawing(owner: string, draw: (handle: OverlayHandle) => () => void, onRemoved: () => void): OverlayHandle {
    return register({owner, kind: DRAWING_KIND, lifetime: {kind: "scene"}, onReplace: "keep", draw, onRemoved});
}

// ----- a model's nodes, placed by the plugin ---------------------------------------

/** `SceneHandle.poseModel`: the viewport on screen places the nodes (viewport/api/modelPose.ts);
 *  what is kept here is that the owner made it. */
export function poseModel(owner: string, sourceName: string, names: readonly string[]): SceneModelPose {
    const pose = createModelPose(sourceName, names);
    const release = own(owner, () => pose.remove());
    return {place: (matrices) => pose.place(matrices), found: () => pose.found(), remove: release};
}

// ----- where the scene's coordinates are in the viewport ---------------------------

/** What is drawn now, each as the function that moves it to where the viewport places the model now. */
const placed = new Set<() => void>();
let offFrame: (() => void) | null = null;

/** The scene's own coordinates in the viewport's world, as a matrix; null when they are the same.
 *  Either viewport recentres what it loads and publishes by how much (viewport/api/sceneFrame,
 *  state/sceneFrameStore). */
function sceneMatrix(): number[] | null {
    const offset = useSceneFrameStore.getState().offset;
    if (!offset || (offset[0] === 0 && offset[1] === 0 && offset[2] === 0)) return null;
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, offset[0], offset[1], offset[2], 1];
}

function follow(move: (matrix: number[] | null) => void): () => void {
    const held = (): void => move(sceneMatrix());
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
// One per owner and kind, shared by everything the owner draws through it; the manager releases it
// with the last of them.

const layerName = (owner: string): string => `plugin:${owner}:layers`;
const rowId = (owner: string, id: string): string => `${owner}:${id}`;

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

    /** What it is drawn with now: the plugin's own look and its row's. */
    get drawnVisible(): boolean {
        return this.visible && this.rowVisible;
    }

    get drawnOpacity(): number {
        return this.opacity * this.rowOpacity;
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
        this.apply(this.drawnVisible, this.drawnOpacity);
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

/** Where a thing is drawn now: the viewport's layer and its handle in it. */
interface DrawnIn<L> {
    layer: L;
    handle: number;
}

// ----- lines ------------------------------------------------------------------

const NO_LINES: SceneLineLayer = {setSegments: () => {}, setPolyline: () => {}, setLook: () => {}, remove: () => {}};

export function addLineLayer(owner: string, spec: SceneLineLayerSpec = {}): SceneLineLayer {
    if (!activeViewport()) return NO_LINES;
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
    // What it draws, as last set: drawn again on a viewport that comes on screen.
    let data = segments;
    let count = segments.length / 6;
    let color = linearColor(spec.color ?? "#ffffff");
    let at: DrawnIn<OverlayLayer> | null = null;
    const look = new Look(spec, (visible, opacity) => at?.layer.setLook(at.handle, {visible, opacity}));
    const entry = drawing(
        owner,
        (h) => {
            const layer = h.lines(layerName(owner))!;
            // A layer of no segments yet is made with one of no length: there is nothing to draw of it.
            const empty = count === 0;
            const handle = layer.addLines({
                segments: empty ? new Float32Array(6) : data.length === count * 6 ? data : data.subarray(0, count * 6),
                color,
                widthPx: spec.widthPx,
                opacity: look.drawnOpacity,
                onTop: !!spec.throughModel,
                clipped: true,
                matrix: sceneMatrix(),
            });
            if (empty) layer.setGeometry(handle, data, 0);
            if (!look.drawnVisible) layer.setLook(handle, {visible: false});
            at = {layer, handle};
            const unfollow = follow((matrix) => layer.setLook(handle, {matrix}));
            return () => {
                unfollow();
                at = null;
                layer.remove(handle);
            };
        },
        () => look.release(),
    );
    look.join(owner, spec.layer);
    return {
        setSegments: (next, n) => {
            if (!entry.alive) return;
            data = next;
            count = Math.max(0, Math.min(n ?? Infinity, Math.floor(next.length / 6)));
            at?.layer.setGeometry(at.handle, next, n);
        },
        setPolyline: (points, n) => {
            if (!entry.alive) return;
            const vertices = Math.max(0, Math.min(n ?? Infinity, Math.floor(points.length / 3)));
            scratch = room(scratch, Math.max(0, vertices - 1) * 6);
            data = scratch;
            count = polylineSegments(points, vertices, scratch);
            at?.layer.setGeometry(at.handle, scratch, count);
        },
        setLook: (change) => {
            if (!entry.alive) return;
            if (change.color !== undefined) {
                color = linearColor(change.color);
                at?.layer.setLook(at.handle, {color});
            }
            look.set(change);
        },
        remove: () => entry.remove(),
    };
}

// ----- points -----------------------------------------------------------------

const NO_POINTS: ScenePointLayer = {setPoints: () => {}, setLook: () => {}, remove: () => {}};

export function addPointLayer(owner: string, spec: ScenePointLayerSpec = {}): ScenePointLayer {
    if (!activeViewport()) return NO_POINTS;
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
    let data = positions;
    let count = positions.length / 3;
    let color = linearColor(spec.color ?? "#ffffff");
    let at: DrawnIn<OverlayLayer> | null = null;
    const look = new Look(spec, (visible, opacity) => at?.layer.setLook(at.handle, {visible, opacity}));
    const entry = drawing(
        owner,
        (h) => {
            const layer = h.lines(layerName(owner))!;
            const empty = count === 0;
            const handle = layer.addPoints({
                positions: empty ? new Float32Array(3) : data.length === count * 3 ? data : data.subarray(0, count * 3),
                colors: colored ? (empty ? new Float32Array(3) : tint.subarray(0, count * 3)) : undefined,
                color: colored ? undefined : color,
                sizePx: spec.sizePx,
                opacity: look.drawnOpacity,
                onTop: !!spec.throughModel,
                clipped: true,
                matrix: sceneMatrix(),
            });
            if (empty) layer.setGeometry(handle, data, 0);
            if (!look.drawnVisible) layer.setLook(handle, {visible: false});
            at = {layer, handle};
            const unfollow = follow((matrix) => layer.setLook(handle, {matrix}));
            return () => {
                unfollow();
                at = null;
                layer.remove(handle);
            };
        },
        () => look.release(),
    );
    look.join(owner, spec.layer);
    return {
        setPoints: (next, n, colors) => {
            if (!entry.alive) return;
            const points = Math.max(0, Math.min(n ?? Infinity, Math.floor(next.length / 3)));
            data = next;
            count = points;
            if (colored) {
                tint = room(tint, points * 3);
                if (colors) linearInto(colors, Math.min(points * 3, colors.length));
            }
            if (colored && colors) at?.layer.setGeometry(at.handle, next, points, tint);
            else at?.layer.setGeometry(at.handle, next, points);
        },
        setLook: (change) => {
            if (!entry.alive) return;
            if (change.color !== undefined && !colored) {
                color = linearColor(change.color);
                at?.layer.setLook(at.handle, {color});
            }
            look.set(change);
        },
        remove: () => entry.remove(),
    };
}

// ----- images -----------------------------------------------------------------

const NO_IMAGE: SceneImage = {setPixels: () => {}, setRect: () => {}, setLook: () => {}, unavailable: () => null, remove: () => {}};

export function addImage(owner: string, image: SceneImageSpec): SceneImage {
    if (!activeViewport()) return NO_IMAGE;
    if (image.pixels.length < image.width * image.height * 4) {
        throw new Error(`addImage: pixels needs 4 bytes per pixel (${image.width * image.height * 4}), got ${image.pixels.length}`);
    }
    let width = image.width, height = image.height;
    let pixels = image.pixels;
    let rect: ImageRect = {origin: image.origin, u: image.u, v: image.v};
    let at: DrawnIn<ImageLayer> | null = null;
    const look = new Look(image, (visible, opacity) => at?.layer.setLook(at.handle, {visible, opacity}));
    const entry = drawing(
        owner,
        (h) => {
            const layer = h.images(layerName(owner))!;
            const handle = layer.add({
                ...rect,
                width,
                height,
                pixels,
                opacity: look.drawnOpacity,
                visible: look.drawnVisible,
                onTop: !!image.throughModel,
                nearest: !!image.nearest,
                clipped: true,
                matrix: sceneMatrix(),
            });
            at = {layer, handle};
            const unfollow = follow((matrix) => layer.setLook(handle, {matrix}));
            return () => {
                unfollow();
                at = null;
                layer.remove(handle);
            };
        },
        () => look.release(),
    );
    look.join(owner, image.layer);
    return {
        setPixels: (next, w, h) => {
            if (!entry.alive) return;
            width = w ?? width;
            height = h ?? height;
            pixels = next;
            at?.layer.setPixels(at.handle, next, w, h);
        },
        setRect: (next) => {
            if (!entry.alive) return;
            rect = {origin: next.origin, u: next.u, v: next.v};
            at?.layer.setRect(at.handle, next);
        },
        setLook: (change) => {
            if (entry.alive) look.set(change);
        },
        // Removed, it is not drawn at all; that is not something the viewport cannot do.
        unavailable: () => (entry.alive && at ? at.layer.unavailable(width, height) : null),
        remove: () => entry.remove(),
    };
}

/** Why the viewport on screen cannot draw an image of this size; null when it can (or there is none). */
export function imageUnavailable(width: number, height: number): string | null {
    if (!activeViewport()) return null;
    // Asked of a layer of the viewport's, made for the asking and released again.
    const probe = register({owner: "plugin:probe", lifetime: {kind: "until-removed"}});
    try {
        return probe.images("plugin:probe")?.unavailable(width, height) ?? null;
    } finally {
        probe.remove();
    }
}

// ----- the playback clock and the Layers panel, as one plugin sees them ---------------------

/** The release of each plugin marker source (`owner:sourceId`) that has markers on the timeline. */
const markerReleases = new Map<string, () => void>();

export function makePluginPlayback(owner: string): PluginPlayback {
    const sourceOf = (sourceId: string) => `${owner}:${sourceId}`;
    return {
        addTrack: (track) => {
            const handle = playbackClock.addTrack({
                id: `${owner}:${track.id}`,
                duration: track.duration,
                show: track.show,
                label: track.label,
                samples: track.samples,
                stepSample: track.stepSample,
            });
            const remove = own(owner, handle.remove);
            return {setDuration: handle.setDuration, setSamples: handle.setSamples, remove};
        },
        setMarkers: (sourceId, markers) => {
            const source = sourceOf(sourceId);
            playbackClock.setMarkers(source, markers ?? []);
            const has = playbackClock.markers().some((m) => m.source === source);
            if (has && !markerReleases.has(source)) {
                markerReleases.set(
                    source,
                    own(owner, () => {
                        markerReleases.delete(source);
                        playbackClock.clearMarkers(source);
                    }),
                );
            } else if (!has) {
                markerReleases.get(source)?.();
            }
        },
        clearMarkers: (sourceId) => {
            const source = sourceOf(sourceId);
            const release = markerReleases.get(source);
            if (release) release();
            else playbackClock.clearMarkers(source);
        },
        markers: () => playbackClock.markers(),
        seekMarker: (direction) => playbackClock.seekMarker(direction),
        step: (direction, size, mods) => playbackClock.step(direction, size, mods),
        nextSample: (t, direction) => playbackClock.nextSample(t, direction),
        captureFrame: (options) => captureFrame(options),
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
