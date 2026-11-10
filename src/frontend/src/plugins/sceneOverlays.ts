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
import type {Billboard, BillboardLayer} from "@/viewport/api/billboards";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {activeViewport, type ViewportApi} from "@/viewport/api/viewportApi";
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

// ----- colours ----------------------------------------------------------------

/** One sRGB channel (0..1) as linear light. */
function channel(c: number): number {
    return c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4);
}

// A CSS colour is parsed once: painting 20,000 elements names a handful of colours.
const parsed = new Map<string, readonly [number, number, number]>();
const MAX_PARSED = 4096;
let cssContext: CanvasRenderingContext2D | null | undefined;

/** A CSS colour as sRGB channels in 0..1: hex and rgb() here, anything else as the browser reads it. */
function cssChannels(css: string): [number, number, number] {
    const text = css.trim();
    const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text)?.[1];
    if (hex) {
        const wide = hex.length === 3 ? hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2] : hex;
        const n = parseInt(wide, 16);
        return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
    }
    const rgb = /^rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)/i.exec(text);
    if (rgb) return [Math.min(255, +rgb[1]) / 255, Math.min(255, +rgb[2]) / 255, Math.min(255, +rgb[3]) / 255];
    if (cssContext === undefined) cssContext = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
    if (cssContext) {
        // The browser gives back "#rrggbb" (or rgba() for a see-through colour) for whatever it understood.
        cssContext.fillStyle = "#ffffff";
        cssContext.fillStyle = text;
        const read = String(cssContext.fillStyle);
        if (read !== text) return cssChannels(read);
    }
    console.warn(`[plugins] unknown colour "${css}"`);
    return [1, 1, 1];
}

/** `c` as linear RGB into `out[at .. at + 2]`. */
function linearInto(c: SceneColor, out: Float32Array | number[], at = 0): void {
    if (typeof c !== "string") {
        out[at] = channel(c[0]);
        out[at + 1] = channel(c[1]);
        out[at + 2] = channel(c[2]);
        return;
    }
    let rgb = parsed.get(c);
    if (!rgb) {
        const [r, g, b] = cssChannels(c);
        rgb = [channel(r), channel(g), channel(b)];
        if (parsed.size >= MAX_PARSED) parsed.clear();
        parsed.set(c, rgb);
    }
    out[at] = rgb[0];
    out[at + 1] = rgb[1];
    out[at + 2] = rgb[2];
}

function linear(c: SceneColor): [number, number, number] {
    const out: [number, number, number] = [0, 0, 0];
    linearInto(c, out);
    return out;
}

// ----- what an owner has on screen ---------------------------------------------

/** Something drawn for a plugin: how to take it away, and the FEA result it is pinned to (null
 *  for none), with which it leaves the screen. */
interface Drawn {
    take(): void;
    result: object | null;
    /** Moves it to where the result is drawn now; called before each frame. */
    follow: (() => void) | null;
}

const owned = new Map<string, Set<Drawn>>();
/** Everything that follows a result, and the frame hook while there is any. */
const following = new Set<Drawn>();
let offBeforeDraw: (() => void) | null = null;
let offTargetGone: (() => void) | null = null;

function followAll(): void {
    for (const drawn of following) drawn.follow!();
}

function track(owner: string, drawn: Drawn): SceneOverlay {
    let set = owned.get(owner);
    if (!set) owned.set(owner, (set = new Set()));
    set.add(drawn);
    if (drawn.follow) {
        following.add(drawn);
        offBeforeDraw ??= onBeforeDraw(followAll);
    }
    // A result that leaves the screen takes what was drawn in its coordinates along.
    offTargetGone ??= onFeaTargetGone((result) => {
        for (const [who, all] of owned) for (const d of [...all]) if (d.result === result) dispose(who, d);
    });
    requestRender();
    return {remove: () => dispose(owner, drawn)};
}

function dispose(owner: string, drawn: Drawn): void {
    const set = owned.get(owner);
    if (!set?.delete(drawn)) return;
    if (following.delete(drawn) && following.size === 0) {
        offBeforeDraw?.();
        offBeforeDraw = null;
    }
    drawn.take();
    // The owner's last overlay: its layers go too.
    if (set.size === 0) {
        owned.delete(owner);
        const layers = layersOf.get(owner);
        layersOf.delete(owner);
        layers?.lines?.dispose();
        layers?.labels?.dispose();
    }
    requestRender();
}

/** Remove every overlay `owner` added through these verbs (SceneHandle.remove(owner) calls this). */
export function removeOwnerOverlays(owner: string): void {
    for (const drawn of [...(owned.get(owner) ?? [])]) dispose(owner, drawn);
}

const layerName = (owner: string): string => `plugin:${owner}`;

/** The layers an owner draws through, in the viewport they were made in: made when first drawn
 *  to, released with the owner's last overlay. */
const layersOf = new Map<string, {viewport: ViewportApi; lines: OverlayLayer | null; labels: BillboardLayer | null}>();

function layers(owner: string, viewport: ViewportApi): {lines: OverlayLayer | null; labels: BillboardLayer | null} {
    let entry = layersOf.get(owner);
    // Another viewport took the place of the one the owner drew in: what it draws now goes there.
    if (entry?.viewport !== viewport) layersOf.set(owner, (entry = {viewport, lines: null, labels: null}));
    return entry;
}

const lineLayer = (owner: string, viewport: ViewportApi): OverlayLayer => (layers(owner, viewport).lines ??= viewport.overlays(layerName(owner)));
const labelLayer = (owner: string, viewport: ViewportApi): BillboardLayer => (layers(owner, viewport).labels ??= viewport.billboards(layerName(owner)));

// ----- lines ------------------------------------------------------------------

type LineLook = Pick<SceneLines, "color" | "widthPx" | "dashed" | "opacity" | "throughModel">;

/** A line overlay in the result's coordinates (or the world's without one); null without a viewport. */
function drawLines(owner: string, segments: Float32Array, look: LineLook, colors?: Float32Array): {drawn: Drawn; move: (positions: Float32Array) => void} | null {
    const viewport = activeViewport();
    if (!viewport) return null;
    const target = activeFeaTarget();
    const layer = lineLayer(owner, viewport);
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
        drawn: {take: () => layer.remove(handle), result: target?.token ?? null, follow: null},
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
    const lines = drawLines(owner, positions, spec, colors);
    return lines ? track(owner, lines.drawn) : NOTHING;
}

/** The outline of `outline.elements`' faces on the active FEA mesh, kept on the warped surface. */
export function addElementOutlines(owner: string, outline: SceneElementOutline): SceneOverlay | null {
    const geometry = activeFeaTarget()?.elements("model");
    if (!geometry) return null;
    // The edges are found once, on the mesh's own vertices; a warp moves them, it does not change them.
    const edges = boundaryEdges(geometry.rest, elementVertices(geometry, outline.elements));
    if (!edges.length) return null;
    const positions = placeVertices(geometry, edges, new Float32Array(edges.length * 3));
    const lines = drawLines(owner, positions, outline);
    if (!lines) return NOTHING;
    let placedAt = geometry.version();
    lines.drawn.follow = () => {
        const now = geometry.version();
        if (now === placedAt) return;
        placedAt = now;
        lines.move(placeVertices(geometry, edges, positions));
    };
    return track(owner, lines.drawn);
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

export function addLabels(owner: string, labels: SceneLabel[], opts?: SceneLabelOptions): SceneOverlay {
    const viewport = activeViewport();
    if (!viewport) return NOTHING;
    const heightPx = opts?.heightPx ?? 18;
    const target = activeFeaTarget();
    const geometry = target?.elements("model") ?? null;
    const frame = target?.matrix() ?? null;
    const layer = labelLayer(owner, viewport);
    const handles: number[] = [];
    /** The labels pinned to elements: what `follow` moves. */
    const pinned: {handle: number; billboard: Billboard; position: [number, number, number]; vertices: number[]}[] = [];
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
        const handle = layer.add(billboard);
        handles.push(handle);
        if (vertices) pinned.push({handle, billboard, position, vertices});
    }
    let placedAt = geometry && pinned.length ? geometry.version() : 0;
    return track(owner, {
        take: () => {
            for (const handle of handles) layer.remove(handle);
        },
        result: target?.token ?? null,
        follow: geometry && pinned.length
            ? () => {
                const now = geometry.version();
                if (now === placedAt) return;
                placedAt = now;
                for (const p of pinned) {
                    centreInto(geometry, p.vertices, p.position);
                    layer.update(p.handle, p.billboard);
                }
            }
            : null,
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
    return painted ? track(owner, {take: () => painted.remove(), result: target.token, follow: null}) : null;
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

/** Frame the camera on everything loaded, as the toolbar's zoom-to-all does. */
export function frameAll(): boolean {
    const viewport = activeViewport();
    if (!viewport) return false;
    viewport.fitAll();
    return true;
}
