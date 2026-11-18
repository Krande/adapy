// The measurements drawn in the scene, on whichever viewport is on screen: points, lines, the arc of an
// angle and a label with the value, through the viewport's overlay layers and billboards
// (viewport/api/overlayLayer.ts, billboards.ts) -- what core's own overlays are drawn through on both
// viewports. Drawn over the model, so a measurement is seen from anywhere. No three.js.
import {useMeasureStore, clearMeasurements, type Measurement, type MeasurePoint} from "@/state/measureStore";
import {requestRender} from "@/state/perfStore";
import type {BillboardLayer, Billboard} from "@/viewport/api/billboards";
import type {OverlayLayer} from "@/viewport/api/overlayLayer";
import {followActiveViewport, onModelLoaded, type ViewportApi} from "@/viewport/api/viewportApi";
import {arcMiddle, arcSegments, distance, lerp, type Vec3} from "./measureMath";
import {summarise} from "./measurements";
import type {SnapKind, SnapResult} from "./snap";

export const MEASURE_LAYER = "core:measure";
export const MEASURE_HOVER_LAYER = "core:measure-hover";

type Rgb = readonly [number, number, number];
/** Linear rgb. */
export const MEASURE_COLORS = {
    line: [1, 0.45, 0] as Rgb,
    point: [1, 1, 1] as Rgb,
    x: [0.9, 0.06, 0.06] as Rgb,
    y: [0.06, 0.65, 0.06] as Rgb,
    z: [0.08, 0.25, 1] as Rgb,
};
/** The marker under the pointer, by what it snaps to: colour and size (CSS pixels). */
export const SNAP_MARKERS: Readonly<Record<SnapKind, {color: Rgb; sizePx: number}>> = {
    corner: {color: [1, 0.2, 0.6], sizePx: 13},
    midpoint: {color: [0, 0.75, 1], sizePx: 11},
    edge: {color: [1, 0.85, 0], sizePx: 9},
    surface: {color: [0.8, 0.8, 0.8], sizePx: 7},
};

const LABEL_PX = 18;
const LABEL_LINE_PX = 64; // canvas pixels per line

const flat = (points: readonly Vec3[]): Float32Array => Float32Array.from(points.flatMap((p) => [p[0], p[1], p[2]]));
const segmentsOf = (pairs: readonly (readonly [Vec3, Vec3])[]): Float32Array => Float32Array.from(pairs.flatMap(([a, b]) => [...a, ...b]));

/** A label's image: the value on a dark rounded plate. */
function labelImage(text: string): Billboard["image"] {
    return {
        key: `core:measure-label:${text}`,
        crisp: true,
        draw: () => {
            const h = LABEL_LINE_PX;
            const canvas = document.createElement("canvas");
            const ctx = canvas.getContext("2d")!;
            const font = `bold ${Math.round(h * 0.55)}px sans-serif`;
            ctx.font = font;
            canvas.width = Math.max(h, Math.ceil(ctx.measureText(text).width + h * 0.5));
            canvas.height = h;
            ctx.beginPath();
            ctx.roundRect(2, 2, canvas.width - 4, h - 4, h * 0.25);
            ctx.fillStyle = "rgba(15, 23, 42, 0.85)";
            ctx.fill();
            ctx.lineWidth = 3;
            ctx.strokeStyle = "#ff9a1f";
            ctx.stroke();
            ctx.font = font; // resizing the canvas reset it
            ctx.fillStyle = "#ffffff";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(text, canvas.width / 2, h / 2 + 2);
            return canvas;
        },
    };
}

function label(labels: BillboardLayer, at: Vec3, text: string): void {
    labels.add({position: [at[0], at[1], at[2]], image: labelImage(text), height: LABEL_PX, sizing: "screen", anchor: [0.5, -0.2], order: 14});
}

const LOOK = {onTop: true, fitted: false} as const;

/** Draw one measurement (or the one being made, `pending`). */
export function drawMeasurement(lines: OverlayLayer, labels: BillboardLayer, m: Pick<Measurement, "tool" | "points">, pending = false): void {
    const w = m.points.map((p) => p.world);
    if (!w.length) return;
    lines.addPoints({positions: flat(w), color: MEASURE_COLORS.point, sizePx: 8, ...LOOK});
    const done = !pending;
    switch (m.tool) {
        case "distance": {
            if (w.length < 2) return;
            lines.addLines({segments: segmentsOf([[w[0], w[1]]]), color: MEASURE_COLORS.line, widthPx: 3, ...LOOK});
            // The components, as the sides of the box the distance is the diagonal of: x, then y, then z.
            const [a, b] = w;
            const ax: Vec3 = [b[0], a[1], a[2]], axy: Vec3 = [b[0], b[1], a[2]];
            const legs: [Vec3, Vec3, Rgb][] = [[a, ax, MEASURE_COLORS.x], [ax, axy, MEASURE_COLORS.y], [axy, b, MEASURE_COLORS.z]];
            for (const [p, q, color] of legs) {
                const span = distance(p, q);
                if (span === 0) continue;
                lines.addLines({segments: segmentsOf([[p, q]]), color, widthPx: 1.5, dashed: {dash: span / 30, gap: span / 30}, ...LOOK});
            }
            if (done) label(labels, lerp(a, b, 0.5), summarise(m as Measurement).value);
            return;
        }
        case "angle": {
            if (w.length < 2) return;
            lines.addLines({segments: segmentsOf(w.slice(1).map((p, i) => [w[i], p] as [Vec3, Vec3])), color: MEASURE_COLORS.line, widthPx: 3, ...LOOK});
            if (w.length < 3) return;
            const radius = 0.3 * Math.min(distance(w[0], w[1]), distance(w[2], w[1]));
            const arc = arcSegments(w[0], w[1], w[2], radius);
            if (arc.length) lines.addLines({segments: arc, color: MEASURE_COLORS.line, widthPx: 2, ...LOOK});
            if (done) label(labels, arcMiddle(w[0], w[1], w[2], radius), summarise(m as Measurement).value);
            return;
        }
        case "edgeAngle": {
            const edges = m.points.map((p) => p.edge).filter((e): e is readonly [Vec3, Vec3] => !!e);
            if (edges.length) lines.addLines({segments: segmentsOf(edges), color: MEASURE_COLORS.line, widthPx: 4, ...LOOK});
            if (done && w.length === 2) label(labels, lerp(w[0], w[1], 0.5), summarise(m as Measurement).value);
            return;
        }
        case "path": {
            if (w.length >= 2) lines.addLines({segments: segmentsOf(w.slice(1).map((p, i) => [w[i], p] as [Vec3, Vec3])), color: MEASURE_COLORS.line, widthPx: 3, ...LOOK});
            if (done && w.length >= 2) {
                const s = summarise(m as Measurement);
                // Each leg's length at its middle while there are few enough to read; the total at the end.
                if (w.length <= 13) s.details.forEach((leg, i) => label(labels, lerp(w[i], w[i + 1], 0.5), leg.replace(/^leg \d+: /, "")));
                label(labels, w[w.length - 1], `Σ ${s.value}`);
            }
            return;
        }
        case "point":
            if (done) label(labels, w[0], summarise(m as Measurement).value);
            return;
    }
}

/** What the pointer snaps to: a marker (its colour and size say which kind), the edge it is on, and
 *  a dashed line from the last point of the measurement being made. */
export function drawHover(lines: OverlayLayer, hover: SnapResult | null, last: MeasurePoint | null): void {
    if (!hover) return;
    const marker = SNAP_MARKERS[hover.kind];
    if (hover.edge) lines.addLines({segments: segmentsOf([hover.edge]), color: marker.color, widthPx: 3, ...LOOK});
    if (last) {
        const span = distance(last.world, hover.world);
        if (span > 0) lines.addLines({segments: segmentsOf([[last.world, hover.world]]), color: MEASURE_COLORS.line, widthPx: 2, dashed: {dash: span / 40, gap: span / 40}, ...LOOK});
    }
    lines.addPoints({positions: flat([hover.world]), color: marker.color, sizePx: marker.sizePx, ...LOOK});
}

/** Draw everything the store holds into the layers, after clearing them. */
export function drawAll(lines: OverlayLayer, labels: BillboardLayer, hoverLines: OverlayLayer): void {
    const {measurements, pending, tool, hover} = useMeasureStore.getState();
    lines.clear();
    labels.clear();
    for (const m of measurements) drawMeasurement(lines, labels, m);
    if (tool && pending.length) drawMeasurement(lines, labels, {tool, points: pending}, true);
    hoverLines.clear();
    drawHover(hoverLines, tool ? hover : null, tool ? (pending[pending.length - 1] ?? null) : null);
}

let unbind: (() => void) | null = null;

/** Keep the measurements drawn on whichever viewport is on screen, from now on (idempotent). A model
 *  that replaces the scene takes them away. */
export function bindMeasureDrawing(): void {
    if (unbind) return;
    const offViewport = followActiveViewport((viewport: ViewportApi) => {
        const lines = viewport.overlays(MEASURE_LAYER);
        const labels = viewport.billboards(MEASURE_LAYER);
        const hoverLines = viewport.overlays(MEASURE_HOVER_LAYER);
        const redraw = () => {
            drawAll(lines, labels, hoverLines);
            requestRender();
        };
        redraw();
        const offStore = useMeasureStore.subscribe((s, p) => {
            if (s.measurements === p.measurements && s.pending === p.pending && s.tool === p.tool) {
                if (s.hover === p.hover) return;
                // Only the marker moved: its layer alone.
                hoverLines.clear();
                drawHover(hoverLines, s.tool ? s.hover : null, s.tool ? (s.pending[s.pending.length - 1] ?? null) : null);
                requestRender();
                return;
            }
            redraw();
        });
        return () => {
            offStore();
            lines.dispose();
            labels.dispose();
            hoverLines.dispose();
        };
    });
    // A model that replaces what is on screen: what was measured is gone with what it was measured on.
    const offLoaded = onModelLoaded((info) => {
        if (!info.added) clearMeasurements();
    });
    unbind = () => {
        offLoaded();
        offViewport();
    };
}

/** Stop drawing (tests). */
export function unbindMeasureDrawing(): void {
    unbind?.();
    unbind = null;
}
