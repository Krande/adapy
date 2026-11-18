// The Measure tab's state: the tool in hand, the measurements made, the one being made and what the
// pointer snaps to now. Plain data: the tab lists it, utils/measure/measureDrawing.ts draws it on
// whichever viewport is on screen, utils/measure/measureController.ts feeds it the clicks. No
// three.js. Measurements are no part of the undo history and never touch the selection.
import {create} from "zustand";

import type {Vec3} from "@/utils/measure/measureMath";
import type {SnapKind, SnapResult} from "@/utils/measure/snap";

export type MeasureTool = "distance" | "angle" | "edgeAngle" | "path" | "point";

export const MEASURE_TOOLS: readonly MeasureTool[] = ["distance", "angle", "edgeAngle", "path", "point"];

/** How many points finish a measurement of each tool; a path takes any number from two, until it is
 *  finished. */
export const TOOL_POINTS: Readonly<Record<MeasureTool, number | null>> = {distance: 2, angle: 3, edgeAngle: 2, path: null, point: 1};

export interface MeasurePoint {
    /** Where it is drawn: the viewer's world (the models recentred by the scene's offset). */
    world: Vec3;
    /** The same point in the models' own coordinates (world minus the scene's offset). */
    model: Vec3;
    /** What it snapped to. */
    kind: SnapKind;
    /** The feature edge it lies on, for an edge or midpoint snap (in the world). */
    edge?: readonly [Vec3, Vec3];
}

export interface Measurement {
    id: number;
    tool: MeasureTool;
    points: MeasurePoint[];
}

interface MeasureState {
    /** The tool taking the scene's clicks; null while none is. */
    tool: MeasureTool | null;
    /** Snap to corners, edge midpoints and edges; off, a click measures the surface point under it. */
    snap: boolean;
    measurements: Measurement[];
    /** The points of the measurement being made. */
    pending: MeasurePoint[];
    /** What the pointer would snap to now. */
    hover: SnapResult | null;
    /** A word for the user about the last click (one the tool could not use). */
    notice: string | null;
}

export const useMeasureStore = create<MeasureState>(() => ({
    tool: null,
    snap: true,
    measurements: [],
    pending: [],
    hover: null,
    notice: null,
}));

let nextId = 1;

/** Take up `tool` (null: put the tool down). What was being measured is dropped. */
export function setMeasureTool(tool: MeasureTool | null): void {
    useMeasureStore.setState({tool, pending: [], hover: null, notice: null});
}

export function setMeasureSnap(snap: boolean): void {
    useMeasureStore.setState({snap});
}

export function setMeasureHover(hover: SnapResult | null): void {
    useMeasureStore.setState({hover});
}

/** Add a clicked point to the measurement being made: "added", "done" when it finished one, or
 *  "refused" when the tool cannot use it (an angle between edges needs points on edges). */
export function addMeasurePoint(point: MeasurePoint): "added" | "done" | "refused" {
    const {tool, pending, measurements} = useMeasureStore.getState();
    if (!tool) return "refused";
    if (tool === "edgeAngle" && !point.edge) {
        useMeasureStore.setState({notice: "Click on an edge: the angle is measured between two edges."});
        return "refused";
    }
    const points = [...pending, point];
    const needed = TOOL_POINTS[tool];
    if (needed !== null && points.length >= needed) {
        useMeasureStore.setState({measurements: [...measurements, {id: nextId++, tool, points}], pending: [], notice: null});
        return "done";
    }
    useMeasureStore.setState({pending: points, notice: null});
    return "added";
}

/** End the measurement being made (Escape, Enter, the tab's Finish): a path of two points or more is
 *  kept, anything else unfinished is dropped. False when nothing was being made. */
export function endPendingMeasurement(): boolean {
    const {tool, pending, measurements} = useMeasureStore.getState();
    if (!pending.length) return false;
    const keep = tool === "path" && pending.length >= 2;
    useMeasureStore.setState({
        measurements: keep ? [...measurements, {id: nextId++, tool: "path", points: pending}] : measurements,
        pending: [],
        notice: null,
    });
    return true;
}

export function removeMeasurement(id: number): void {
    useMeasureStore.setState((s) => ({measurements: s.measurements.filter((m) => m.id !== id)}));
}

/** Every measurement off the screen, and the one being made (the scene was cleared or replaced). */
export function clearMeasurements(): void {
    const s = useMeasureStore.getState();
    if (!s.measurements.length && !s.pending.length && !s.hover) return;
    useMeasureStore.setState({measurements: [], pending: [], hover: null, notice: null});
}
