// What a measurement says, in words: its value and details as the Measure tab lists them, the label
// drawn in the scene, and the text "Copy" puts on the clipboard. No three.js.
import type {Measurement, MeasurePoint, MeasureTool} from "@/state/measureStore";
import {
    angleAt,
    angleBetweenLines,
    components,
    formatAngle,
    formatLength,
    formatPoint,
    formatSigned,
    pathLength,
    sub,
    type Vec3,
} from "./measureMath";

export const TOOL_LABELS: Readonly<Record<MeasureTool, string>> = {
    distance: "Distance",
    angle: "Angle",
    edgeAngle: "Edge angle",
    path: "Path",
    point: "Point",
};

/** What the tab says to do next, by how many points the measurement being made has. */
export function toolPrompt(tool: MeasureTool, have: number): string {
    switch (tool) {
        case "distance":
            return have === 0 ? "Click the first point." : "Click the second point.";
        case "angle":
            return have === 0 ? "Click a point on the first arm." : have === 1 ? "Click the vertex." : "Click a point on the second arm.";
        case "edgeAngle":
            return have === 0 ? "Click the first edge." : "Click the second edge.";
        case "path":
            return have === 0 ? "Click the first point." : have === 1 ? "Click the next point." : "Click the next point; Enter or Escape finishes the path.";
        case "point":
            return "Click a point.";
    }
}

export interface MeasurementSummary {
    /** The value the label shows ("1.234 m", "90.00°"). */
    value: string;
    /** More, a line each (components, legs, coordinates). */
    details: string[];
}

/** A point's coordinates: in the model's own frame, and where the scene recentres the model (the
 *  viewer's world is elsewhere), in the viewer's world too. */
export function pointLines(p: MeasurePoint): string[] {
    const lines = [`model: ${formatPoint(p.model)}`];
    if (p.model.some((v, k) => v !== p.world[k])) lines.push(`viewer: ${formatPoint(p.world)}`);
    return lines;
}

const direction = (edge: readonly [Vec3, Vec3]): Vec3 => sub(edge[1], edge[0]);

export function summarise(m: Measurement): MeasurementSummary {
    const w = m.points.map((p) => p.world);
    switch (m.tool) {
        case "distance": {
            const c = components(w[0], w[1]);
            return {value: formatLength(c.length), details: [`dX ${formatSigned(c.dx)}`, `dY ${formatSigned(c.dy)}`, `dZ ${formatSigned(c.dz)}`]};
        }
        case "angle": {
            const a = angleAt(w[0], w[1], w[2]);
            return {value: a === null ? "-" : formatAngle(a), details: [`arms ${formatLength(dist(w[0], w[1]))}, ${formatLength(dist(w[2], w[1]))}`]};
        }
        case "edgeAngle": {
            const [e1, e2] = m.points.map((p) => p.edge);
            const a = e1 && e2 ? angleBetweenLines(direction(e1), direction(e2)) : null;
            return {value: a === null ? "-" : formatAngle(a), details: a === null ? [] : [`supplement ${formatAngle(180 - a)}`]};
        }
        case "path": {
            const {segments, total} = pathLength(w);
            return {value: formatLength(total), details: segments.map((s, i) => `leg ${i + 1}: ${formatLength(s)}`)};
        }
        case "point":
            return {value: formatPoint(m.points[0].model), details: pointLines(m.points[0])};
    }
}

const dist = (a: Vec3, b: Vec3) => components(a, b).length;

/** Measurements as text, one block each: what "Copy" gives. */
export function measurementsText(list: readonly Measurement[]): string {
    return list
        .map((m, i) => {
            const s = summarise(m);
            const head = `${TOOL_LABELS[m.tool]} ${i + 1}: ${s.value}`;
            const points = m.points.map((p, k) => `  P${k + 1} (${p.kind}) model: ${formatPoint(p.model)}`);
            return [head, ...s.details.map((d) => `  ${d}`), ...(m.tool === "point" ? [] : points)].join("\n");
        })
        .join("\n");
}
