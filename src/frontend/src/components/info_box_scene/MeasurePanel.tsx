import React from "react";

import {
    endPendingMeasurement,
    MEASURE_TOOLS,
    removeMeasurement,
    clearMeasurements,
    setMeasureSnap,
    useMeasureStore,
    type MeasureTool,
} from "@/state/measureStore";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {armMeasureTool, disarmMeasureTool} from "@/utils/measure/measureController";
import {bindMeasureDrawing} from "@/utils/measure/measureDrawing";
import {formatPoint, LENGTH_UNIT} from "@/utils/measure/measureMath";
import {measurementsText, summarise, toolPrompt, TOOL_LABELS} from "@/utils/measure/measurements";
import type {SnapKind} from "@/utils/measure/snap";

// The Scene panel's Measure tab: pick a tool, click the model. Each click snaps to the nearest corner
// of the geometry, else to the middle of an edge or a point on it, else to the surface under the
// pointer (utils/measure/snap.ts). While a tool is in hand it has the scene's clicks -- nothing is
// selected, nothing goes in the undo history -- and leaving the tab puts it down. The measurements
// stay drawn in the scene until they are deleted here or the scene is cleared.

const TOOL_HINTS: Readonly<Record<MeasureTool, string>> = {
    distance: "Distance between two points, with its X, Y and Z components",
    angle: "Angle at a vertex: a point on one arm, the vertex, a point on the other arm",
    edgeAngle: "Angle between two edges of the model",
    path: "Length of a path through several points, per leg and in total",
    point: "Coordinates of one point",
};

const SNAP_WORDS: Readonly<Record<SnapKind, string>> = {
    corner: "corner",
    midpoint: "edge midpoint",
    edge: "edge",
    surface: "surface",
};

const MeasurePanel: React.FC = () => {
    const tool = useMeasureStore((s) => s.tool);
    const snap = useMeasureStore((s) => s.snap);
    const measurements = useMeasureStore((s) => s.measurements);
    const pending = useMeasureStore((s) => s.pending);
    const hover = useMeasureStore((s) => s.hover);
    const notice = useMeasureStore((s) => s.notice);
    const offset = useSceneFrameStore((s) => s.offset);
    const [copied, setCopied] = React.useState(false);

    // What was measured is drawn from the moment the tab is first seen; the tool is put down, and the
    // scene's clicks given back, when the tab is left.
    React.useEffect(() => {
        bindMeasureDrawing();
        return () => disarmMeasureTool();
    }, []);

    const btn = (active: boolean) =>
        "px-2 py-0.5 text-xs rounded-sm transition-colors " + (active ? "bg-blue-700 text-white" : "bg-gray-700 text-gray-200 hover:bg-gray-600");

    const copy = () => {
        const text = measurementsText(measurements);
        void navigator.clipboard?.writeText(text).then(
            () => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
            },
            () => {},
        );
    };
    const recentred = !!offset && offset.some((v) => v !== 0);

    return (
        <div className="flex flex-col gap-2 p-1 text-sm">
            <div className="flex flex-wrap gap-1" role="group" aria-label="Measuring tool">
                {MEASURE_TOOLS.map((t) => (
                    <button
                        key={t}
                        type="button"
                        className={btn(tool === t)}
                        aria-pressed={tool === t}
                        title={TOOL_HINTS[t]}
                        onClick={() => (tool === t ? disarmMeasureTool() : armMeasureTool(t))}
                    >
                        {TOOL_LABELS[t]}
                    </button>
                ))}
            </div>

            <div className="flex items-center gap-2 text-xs">
                <label className="flex items-center gap-1 cursor-pointer" title="Snap to the corners and edges of the geometry">
                    <input type="checkbox" checked={snap} onChange={(e) => setMeasureSnap(e.target.checked)} />
                    Snap to corners and edges
                </label>
            </div>

            {tool ? (
                <div className="text-xs rounded-sm bg-blue-900/30 border border-blue-400/30 px-2 py-1 flex flex-col gap-0.5" data-measure-status>
                    <span>
                        <span className="font-semibold">{TOOL_LABELS[tool]}:</span> {toolPrompt(tool, pending.length)}
                    </span>
                    <span className="text-gray-400">
                        {hover ? `Snaps to: ${SNAP_WORDS[hover.kind]}` : "Point at the model."} Escape ends the measurement; Escape again, or the
                        tool's button, puts the tool down.
                    </span>
                    {notice && <span className="text-amber-300">{notice}</span>}
                    {tool === "path" && pending.length >= 2 && (
                        <button type="button" className={btn(false) + " self-start"} onClick={() => endPendingMeasurement()}>
                            Finish path
                        </button>
                    )}
                </div>
            ) : (
                <p className="text-xs text-gray-400">Pick a tool, then click the model. Clicks measure instead of selecting while a tool is on.</p>
            )}

            {measurements.length > 0 && (
                <div className="flex items-center gap-1">
                    <button type="button" className={btn(false)} onClick={copy} title="Copy every measurement as text">
                        {copied ? "Copied" : "Copy as text"}
                    </button>
                    <button type="button" className={btn(false) + " ml-auto"} onClick={clearMeasurements}>
                        Clear all
                    </button>
                </div>
            )}

            <ul className="flex flex-col gap-1" aria-label="Measurements">
                {measurements.map((m, i) => {
                    const s = summarise(m);
                    return (
                        <li key={m.id} className="border-b border-gray-500/40 pb-1">
                            <div className="flex items-center gap-1">
                                <span className="text-xs text-gray-400">
                                    {TOOL_LABELS[m.tool]} {i + 1}
                                </span>
                                <span className="font-semibold tabular-nums">{s.value}</span>
                                <button
                                    type="button"
                                    className="ml-auto px-1 rounded-sm hover:bg-gray-500/40 text-red-300"
                                    aria-label={`Delete ${TOOL_LABELS[m.tool]} ${i + 1}`}
                                    title="Delete"
                                    onClick={() => removeMeasurement(m.id)}
                                >
                                    ✕
                                </button>
                            </div>
                            {s.details.length > 0 && (
                                <div className="text-xs text-gray-400 tabular-nums">{s.details.map((d) => <div key={d}>{d}</div>)}</div>
                            )}
                        </li>
                    );
                })}
            </ul>

            <p className="text-[10px] text-gray-500">
                Lengths in metres ({LENGTH_UNIT}), the unit of a glTF model. Coordinates are the model's own
                {recentred ? `; the viewer draws the model moved by ${formatPoint(offset!)} (its recentring), shown as "viewer".` : "."}
            </p>
        </div>
    );
};

export default MeasurePanel;
