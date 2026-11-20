// Renderer-neutral scene overlays for plugins: lines, labels and element colours.
//
// A plugin describes WHAT to draw in plain data; core turns it into what the viewport on screen draws
// -- lines through its overlay layer (viewport/api/overlayLayer.ts), labels through its billboards
// (viewport/api/billboards.ts) -- and keeps what is pinned to FEA elements on the deformed surface.
// The FEA result is reached through one lookup (utils/scene/fea/elementSource.ts) whichever way it is
// drawn, so every verb here works on the three.js viewport and on the three.js-free one. A plugin
// that built its own three.js objects instead depended on how one renderer draws them, and on the
// FEA mesh's internals (its draw-range ids, index layout and morph attributes).
//
// Coordinates are the FEA model's own (the active FEA mesh's local frame, which core recentres), or world
// coordinates when no FEA model is loaded. Colours are sRGB, as CSS: "#ffaa00" or [r, g, b] in 0..1.
//
// No three.js in this module or in anything it imports.

import {requestRender} from "@/state/perfStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {
    activeFeaTarget,
    boundaryEdges,
    elementVertices,
    onFeaTargetGone,
    placeVertices,
    toWorld,
    type ElementGeometry,
} from "@/utils/scene/fea/elementSource";
import {onBeforeDraw} from "@/viewport/api/beforeDraw";
import {linearChannel as channel, linearColor as linear, linearColorInto as linearInto} from "@/utils/scene/sceneColor";
import type {Billboard} from "@/viewport/api/billboards";
import {register, removeOwner, removeWhere, type OverlayHandle} from "@/viewport/api/overlayManager";
import {activeViewport, type SelectedObjectBox} from "@/viewport/api/viewportApi";
import {DRAWING_KIND, releaseOwnerLayers} from "./sceneLayers";
import type {
    SceneBox,
    SceneColor,
    SceneElementOutline,
    SceneElementPaint,
    SceneLabel,
    SceneLabelOptions,
    SceneLines,
    SceneOverlay,
} from "./registry";

export {anchorClipVertexShader} from "@/viewport/api/billboards";
export {boundaryEdges} from "@/utils/scene/fea/elementSource";

// ----- what an owner has on screen ---------------------------------------------

// Each thing drawn here is an entry of the overlay manager (viewport/api/overlayManager.ts), tagged as
// a plugin's drawing. In the world, it lives with the scene: a clear removes it, a replace leaves it,
// and a viewport that takes the place of the one it was drawn on draws it again. Pinned to an FEA
// result, it lives with that result and goes with it (a renderer switch draws the result anew).
// The layers an owner draws through (`plugin:<owner>`) are shared by all it draws, and released by
// the manager with the last of them.

/** What moves each drawn thing to where its FEA result is drawn now, and the frame hook while there
 *  is any. */
const following = new Set<() => void>();
let offBeforeDraw: (() => void) | null = null;
let offTargetGone: (() => void) | null = null;

function startFollowing(move: () => void): () => void {
    following.add(move);
    offBeforeDraw ??= onBeforeDraw(() => {
        for (const each of following) each();
    });
    return () => {
        if (following.delete(move) && following.size === 0) {
            offBeforeDraw?.();
            offBeforeDraw = null;
        }
    };
}

/** Keep what `owner` draws: pinned to `result` (null: in the world), drawn by `draw` on the viewport
 *  it is on, and `onRemoved` run when it is removed. */
function track(owner: string, result: object | null, draw?: (h: OverlayHandle) => void | (() => void), onRemoved?: () => void): SceneOverlay {
    // A result that leaves the screen takes what was drawn in its coordinates along.
    offTargetGone ??= onFeaTargetGone((gone) => removeWhere((info) => info.lifetime.kind === "fea-result" && info.lifetime.token === gone));
    const entry = register({
        owner,
        kind: DRAWING_KIND,
        lifetime: result ? {kind: "fea-result", token: result} : {kind: "scene"},
        onReplace: "keep",
        draw,
        onRemoved,
    });
    return {remove: () => entry.remove()};
}

/** Remove every overlay `owner` added through these verbs (SceneHandle.remove(owner) calls this). */
export function removeOwnerOverlays(owner: string): void {
    // What it drew, by these verbs and as line, point and image layers (./sceneLayers.ts).
    removeOwner(owner, DRAWING_KIND);
    // Its playback tracks, posed models and placed boxes.
    releaseOwnerLayers(owner);
}

const layerName = (owner: string): string => `plugin:${owner}`;

// ----- lines ------------------------------------------------------------------

type LineLook = Pick<SceneLines, "color" | "widthPx" | "dashed" | "opacity" | "throughModel">;
type FeaTarget = NonNullable<ReturnType<typeof activeFeaTarget>>;

/** A line overlay in the result's coordinates (or the world's without one), on the viewport `h` is on. */
function drawLines(owner: string, h: OverlayHandle, target: FeaTarget | null, segments: Float32Array, look: LineLook, colors?: Float32Array): {take: () => void; move: (positions: Float32Array) => void} | null {
    const layer = h.lines(layerName(owner));
    if (!layer) return null;
    const handle = layer.addLines({
        segments,
        colors,
        color: colors ? undefined : linear(look.color ?? "#ffffff"),
        widthPx: look.widthPx,
        dashed: look.dashed,
        opacity: look.opacity,
        onTop: !!look.throughModel,
        clipped: true,
        matrix: target?.matrix() ?? null,
    });
    return {
        take: () => layer.remove(handle),
        move: (positions) => layer.setPositions(handle, positions),
    };
}

const NOTHING: SceneOverlay = {remove: () => {}};

export function addLines(owner: string, spec: SceneLines): SceneOverlay {
    const positions = spec.segments instanceof Float32Array ? spec.segments : Float32Array.from(spec.segments);
    const segments = positions.length / 6;
    if (!Number.isInteger(segments)) throw new Error(`addLines: segments needs 6 numbers per segment, got ${positions.length}`);
    if (spec.colors && spec.colors.length !== segments * 3) {
        throw new Error(`addLines: colors needs 3 numbers per segment (${segments * 3}), got ${spec.colors.length}`);
    }
    let colors: Float32Array | undefined;
    if (spec.colors) {
        colors = new Float32Array(spec.colors.length);
        for (let i = 0; i < colors.length; i++) colors[i] = channel(spec.colors[i]);
    }
    if (!activeViewport()) return NOTHING;
    const target = activeFeaTarget();
    return track(owner, target?.token ?? null, (h) => drawLines(owner, h, target, positions, spec, colors)?.take);
}

/** The outline of `outline.elements`' faces on the active FEA mesh, kept on the warped surface. */
export function addElementOutlines(owner: string, outline: SceneElementOutline): SceneOverlay | null {
    const target = activeFeaTarget();
    const geometry = target?.elements("model");
    if (!target || !geometry) return null;
    const all = [...outline.elements];
    // The outline of what is shown of the elements: a hidden element has none, and one shown again
    // gets its back. The edges are found on the mesh's own vertices, again only when what is hidden
    // changes; a warp moves them, it does not change them.
    const outlineOf = (hidden: ReadonlySet<number>) => {
        const shown = hidden.size ? all.filter((e) => !hidden.has(e)) : all;
        const edges = shown.length ? boundaryEdges(geometry.rest, elementVertices(geometry, shown)) : [];
        return {edges, positions: placeVertices(geometry, edges, new Float32Array(edges.length * 3))};
    };
    const hiddenNow = () => new Set(target.hidden());
    if (!outlineOf(new Set()).edges.length) return null;
    if (!activeViewport()) return NOTHING;
    return track(owner, target.token, (h) => {
        let current = outlineOf(hiddenNow());
        let lines = current.edges.length ? drawLines(owner, h, target, current.positions, outline) : null;
        let placedAt = geometry.version();
        let seenVisibility = useVisibilityStore.getState().version;
        const stop = startFollowing(() => {
            const visibility = useVisibilityStore.getState().version;
            if (visibility !== seenVisibility) {
                seenVisibility = visibility;
                lines?.take();
                current = outlineOf(hiddenNow());
                lines = current.edges.length ? drawLines(owner, h, target, current.positions, outline) : null;
                placedAt = geometry.version();
                requestRender();
                return;
            }
            const now = geometry.version();
            if (now === placedAt || !lines) return;
            placedAt = now;
            lines.move(placeVertices(geometry, current.edges, current.positions));
        });
        return () => {
            stop();
            lines?.take();
        };
    });
}

// ----- labels -----------------------------------------------------------------

const LABEL_LINE_PX = 64; // canvas pixels per text line
const LABEL_ORDER = 12;

const linesOf = (label: SceneLabel): string[] => (typeof label.text === "string" ? [label.text] : [...label.text]);

function labelCanvas(label: SceneLabel): HTMLCanvasElement {
    const h = LABEL_LINE_PX;
    const lines = linesOf(label);
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d")!;
    const font = (i: number) => `${lines.length > 1 && i > 0 ? 500 : "bold"} ${Math.round(h * 0.6)}px sans-serif`;
    const circle = label.shape === "circle" && lines.length === 1;
    let textW = 0;
    lines.forEach((line, i) => {
        ctx.font = font(i);
        textW = Math.max(textW, ctx.measureText(line).width);
    });
    canvas.width = circle ? h : Math.max(h, Math.ceil(textW + h * 0.5));
    canvas.height = h * lines.length;
    if (label.background || label.border) {
        const inset = label.border ? 5 : 2;
        const [w, ht] = [canvas.width - inset, canvas.height - inset];
        ctx.beginPath();
        if (circle) ctx.arc(h / 2, h / 2, h / 2 - inset, 0, Math.PI * 2);
        else if (label.shape === "rounded") ctx.roundRect(inset / 2, inset / 2, w, ht, h * 0.25);
        else ctx.rect(inset / 2, inset / 2, w, ht);
        if (label.background) {
            ctx.fillStyle = label.background;
            ctx.fill();
        }
        if (label.border) {
            ctx.lineWidth = 5;
            ctx.strokeStyle = label.border;
            ctx.stroke();
        }
    }
    ctx.fillStyle = label.color ?? "#0f172a";
    ctx.textBaseline = "middle";
    // One line centred; several left-aligned, as a little table reads.
    ctx.textAlign = lines.length > 1 ? "left" : "center";
    lines.forEach((line, i) => {
        ctx.font = font(i); // resizing the canvas reset it
        ctx.fillText(line, lines.length > 1 ? h * 0.25 : canvas.width / 2, h * i + h / 2 + 2);
    });
    return canvas;
}

/** The centre of `vertices` as drawn now, in the world, into `out`; false when there are none. */
function centreInto(geometry: ElementGeometry, vertices: readonly number[], out: [number, number, number]): boolean {
    if (!vertices.length) return false;
    let x = 0, y = 0, z = 0;
    for (let i = 0; i < vertices.length; i++) {
        geometry.position(vertices[i], SCRATCH, 0);
        x += SCRATCH[0];
        y += SCRATCH[1];
        z += SCRATCH[2];
    }
    const n = vertices.length;
    toWorld(geometry.matrix(), x / n, y / n, z / n, out);
    return true;
}

const SCRATCH = new Float32Array(3);
const ABOVE_RIGHT: readonly [number, number] = [0, -0.15];

const pinnedAny = (labels: readonly SceneLabel[]): boolean => labels.some((l) => l.atElements);

/** A label pinned to elements, as it is drawn. */
interface Pinned {
    handle: number;
    billboard: Billboard;
    position: [number, number, number];
    vertices: number[];
    elements: readonly number[];
}

export function addLabels(owner: string, labels: SceneLabel[], opts?: SceneLabelOptions): SceneOverlay {
    if (!activeViewport()) return NOTHING;
    const heightPx = opts?.heightPx ?? 18;
    const target = activeFeaTarget();
    const geometry = target?.elements("model") ?? null;
    const frame = target?.matrix() ?? null;
    /** The labels pinned to elements: what `follow` moves, and takes away while all its elements
     *  are hidden (`handle` -1 then). */
    const pinned: Pinned[] = [];
    /** Every label, at a point or pinned, in the order given: drawn again on a viewport that comes on screen. */
    const order: (Billboard | Pinned)[] = [];
    const hiddenNow = (): ReadonlySet<number> => new Set(target?.hidden() ?? []);
    let hidden = pinnedAny(labels) ? hiddenNow() : new Set<number>();
    /** A label on elements that are all hidden names nothing on screen. */
    const allHidden = (elements: readonly number[]) => elements.length > 0 && elements.every((e) => hidden.has(e));
    for (const label of labels) {
        const vertices = label.atElements && geometry ? elementVertices(geometry, label.atElements) : null;
        const position: [number, number, number] = [0, 0, 0];
        if (vertices) {
            if (!centreInto(geometry!, vertices, position)) continue;
        } else if (label.position) {
            if (frame) toWorld(frame, label.position[0], label.position[1], label.position[2], position);
            else position.splice(0, 3, label.position[0], label.position[1], label.position[2]);
        } else {
            continue;
        }
        const lines = linesOf(label).length;
        const billboard: Billboard = {
            position,
            // Labels that read the same share one image.
            image: {
                key: JSON.stringify([label.text, label.color, label.background, label.border, label.shape]),
                draw: () => labelCanvas(label),
                crisp: true,
            },
            height: heightPx * lines,
            sizing: "screen",
            anchor: label.anchor === "above-right" ? ABOVE_RIGHT : undefined,
            order: LABEL_ORDER,
            // Cut away with the point it names, whole.
            clipped: true,
        };
        if (vertices) pinned.push({handle: -1, billboard, position, vertices, elements: [...label.atElements!]});
        order.push(vertices ? pinned[pinned.length - 1] : billboard);
    }
    return track(owner, target?.token ?? null, (h) => {
        const layer = h.labels(layerName(owner));
        if (!layer) return;
        // In the order they were given.
        const handles: number[] = [];
        for (const one of order) {
            if ("vertices" in one) one.handle = allHidden(one.elements) ? -1 : layer.add(one.billboard);
            else handles.push(layer.add(one));
        }
        let placedAt = geometry && pinned.length ? geometry.version() : 0;
        let seenVisibility = useVisibilityStore.getState().version;
        const stop = geometry && pinned.length
            ? startFollowing(() => {
                const visibility = useVisibilityStore.getState().version;
                if (visibility !== seenVisibility) {
                    // Hidden, shown or isolated since: a label on hidden elements goes, and comes back with them.
                    seenVisibility = visibility;
                    hidden = hiddenNow();
                    for (const p of pinned) {
                        const off = allHidden(p.elements);
                        if (off && p.handle >= 0) {
                            layer.remove(p.handle);
                            p.handle = -1;
                        } else if (!off && p.handle < 0) {
                            centreInto(geometry, p.vertices, p.position);
                            p.handle = layer.add(p.billboard);
                        }
                    }
                    requestRender();
                }
                const now = geometry.version();
                if (now === placedAt) return;
                placedAt = now;
                for (const p of pinned) {
                    centreInto(geometry, p.vertices, p.position);
                    if (p.handle >= 0) layer.update(p.handle, p.billboard);
                }
            })
            : null;
        return () => {
            stop?.();
            for (const handle of handles) layer.remove(handle);
            for (const p of pinned) if (p.handle >= 0) layer.remove(p.handle);
            for (const p of pinned) p.handle = -1;
        };
    });
}

// ----- FEA elements -------------------------------------------------------------

export const hasFeaModel = (): boolean => activeFeaTarget() !== null;

/** Colour elements of the active FEA mesh, over the model; null when no FEA model is loaded. */
export function paintFeaElements(owner: string, paint: SceneElementPaint): SceneOverlay | null {
    const target = activeFeaTarget();
    if (!target) return null;
    const neutral = paint.neutral === undefined ? null : paint.neutral;
    const only = paint.only ? new Set(paint.only) : null;
    const painted = target.paint({
        on: paint.on ?? "model",
        name: `${owner}-element-paint`,
        colorOf: (label, out) => {
            if (only && !only.has(label)) return false;
            const color = paint.colors.get(label) ?? neutral;
            if (color == null) return false;
            linearInto(color, out);
            return true;
        },
    });
    return painted ? track(owner, target.token, undefined, () => painted.remove()) : null;
}

/** Show only `elements` of the active FEA mesh (null: show all). */
export function isolateFeaElements(elements: Iterable<number> | null): void {
    activeFeaTarget()?.isolate(elements);
}

/** Hide `elements` of the active FEA mesh, on top of what is hidden already. */
export function hideFeaElements(elements: Iterable<number>): void {
    activeFeaTarget()?.hide(elements);
}

/** The active FEA mesh's hidden elements. */
export function hiddenFeaElements(): number[] {
    return activeFeaTarget()?.hidden() ?? [];
}

export function setFeaElementEdgesVisible(visible: boolean): void {
    useFeaAnimationStore.getState().setElementEdgesVisible(visible);
    requestRender();
}

// ----- camera ---------------------------------------------------------------

/** Frame the camera on `elements` of the FEA model, where they are drawn now (warp included; beams
 *  drawn as solids count with their solids). False when none of them is in the model. */
export function frameFeaElements(elements: Iterable<number>): boolean {
    const target = activeFeaTarget();
    const viewport = activeViewport();
    if (!target || !viewport) return false;
    const wanted = [...elements];
    const min: [number, number, number] = [Infinity, Infinity, Infinity];
    const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
    const world: [number, number, number] = [0, 0, 0];
    for (const on of ["model", "beamSolids"] as const) {
        const geometry = target.elements(on);
        if (!geometry) continue;
        const matrix = geometry.matrix();
        for (const vertex of elementVertices(geometry, wanted)) {
            geometry.position(vertex, SCRATCH, 0);
            toWorld(matrix, SCRATCH[0], SCRATCH[1], SCRATCH[2], world);
            for (let k = 0; k < 3; k++) {
                if (world[k] < min[k]) min[k] = world[k];
                if (world[k] > max[k]) max[k] = world[k];
            }
        }
    }
    return min[0] <= max[0] && viewport.fitBox(min, max);
}

/** Frame the camera on a world-space box. */
export function frameSceneBox(box: SceneBox): boolean {
    return activeViewport()?.fitBox(box.min, box.max) ?? false;
}

/** The selected objects with their boxes in the model's own coordinates, from the viewport on
 *  screen (`SceneHandle.selectionBoxes`); [] without one. */
export function selectionBoxes(): SelectedObjectBox[] {
    return activeViewport()?.selectionBoxes() ?? [];
}

/** Frame the camera on everything loaded, as the toolbar's zoom-to-all does. */
export function frameAll(): boolean {
    const viewport = activeViewport();
    if (!viewport) return false;
    viewport.fitAll();
    return true;
}
