// Number labels over the 3D view: node ids and element ids of a loaded FEA
// result, as HTML in screen space (`utils/scene/fea/numberLabels` decides which
// are worth drawing). Mounted by the viewport overlay layer when a shell asks for
// it (`UiShellSpec.chrome.overlays.labels`), or mounted by a shell over its own
// view. The toggles are core commands (`core:labels-nodes`, `core:labels-elements`,
// `core:labels-values`: each element labelled with its value of the shown result or of a
// property, `useLabelsStore.valueField`).
//
// A pass costs a bounded scan of the mesh, and runs only when something it
// depends on changed (camera, view size, selection, deformation, toggles), at
// most every 60 ms. The result is read as plain data and the camera through the
// viewport on screen (`ViewportApi.projectToScreen`, `onCameraChange`), so the
// labels are the same on either viewport.

import React from "react";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useLabelsStore} from "@/state/labelsStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {placeLabels, type ScreenLabel} from "@/utils/scene/fea/labelPlacement";
import {threeLabelSource} from "@/utils/scene/fea/numberLabels";
import {layerLabelSource, layerSelectedElements} from "@/utils/scene/fea/streaming/layerSession";
import {activeViewport, followActiveViewport, subscribeSelection} from "@/viewport/api/viewportApi";
import {loadNodeLabelTable} from "@/utils/scene/fea/nodeLabelTable";
import {loadElementValueTable, type ElementValueTable} from "@/utils/scene/fea/elementValueTable";

/** The most labels on screen, and the most elements examined per pass. */
export const LABEL_CAP = 400;
export const LABEL_SCAN_CAP = 4000;

const MIN_INTERVAL_MS = 60;

export function NumberLabels() {
    const nodes = useLabelsStore((s) => s.nodes);
    const elements = useLabelsStore((s) => s.elements);
    const values = useLabelsStore((s) => s.values);
    const valueField = useLabelsStore((s) => s.valueField);
    // What the shown result is, so the value labels follow a change of field, step, layer or reduction.
    const shown = useFeaAnimationStore((s) => `${s.fieldName}|${s.stepIndex}|${s.layer}|${s.ipReduction}|${s.reduction}`);
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const [labels, setLabels] = React.useState<ScreenLabel[]>([]);
    const table = React.useRef<number[] | null>(null);
    const [tableFor, setTableFor] = React.useState<unknown>(null);
    const valueTable = React.useRef<ElementValueTable | null>(null);
    const [valuesFor, setValuesFor] = React.useState<unknown>(null);

    // The per-element values to label with, fetched once per (field, step, layer, reduction).
    React.useEffect(() => {
        valueTable.current = null;
        setValuesFor(null);
        if (!values || !sessionActive) return;
        let live = true;
        void loadElementValueTable(valueField).then((t) => {
            if (!live) return;
            valueTable.current = t;
            setValuesFor(t ?? "none");
        });
        return () => {
            live = false;
        };
    }, [values, valueField, sessionActive, manifest, shown]);

    // The solver node ids, fetched once per result.
    React.useEffect(() => {
        table.current = null;
        if (!nodes || !sessionActive) return;
        let live = true;
        void loadNodeLabelTable().then((t) => {
            if (!live) return;
            table.current = t;
            setTableFor(t);
        });
        return () => {
            live = false;
        };
    }, [nodes, sessionActive, manifest]);

    React.useEffect(() => {
        if ((!nodes && !elements && !values) || !sessionActive) {
            setLabels([]);
            return;
        }
        let raf = 0;
        let last = 0;
        let dirty = false;
        const pass = (now: number) => {
            raf = 0;
            if (!dirty) return;
            // At most one pass per interval: the rest of a burst of changes waits for the next.
            if (now - last < MIN_INTERVAL_MS) {
                raf = requestAnimationFrame(pass);
                return;
            }
            dirty = false;
            last = now;
            const viewport = activeViewport();
            const mesh = useFeaAnimationStore.getState().mesh;
            // The result as plain data: over the three.js mesh, or from the result layers of a
            // viewport that has no such mesh.
            const source = mesh ? threeLabelSource(mesh) : layerLabelSource();
            if (!viewport || !source) {
                setLabels((shown) => (shown.length ? [] : shown));
                return;
            }
            const selected = mesh ? (useSelectedObjectStore.getState().selectedObjects.get(mesh) ?? null) : layerSelectedElements();
            const t = table.current;
            setLabels(
                placeLabels(source, (world, count, out) => viewport.projectToScreen(world, count, out), {
                    nodes,
                    elements,
                    values,
                    valueText: valueTable.current ? (n) => valueTable.current?.text(n) : undefined,
                    cap: LABEL_CAP,
                    scanCap: LABEL_SCAN_CAP,
                    selected,
                    nodeText: t ? (i) => String(t[i] ?? i + 1) : undefined,
                }),
            );
        };
        // Whatever a label's place or text depends on asks for a pass when it changes: the camera
        // and the view's size (told by the viewport on screen), the selection, what is hidden,
        // the deformation and the step.
        const stale = () => {
            dirty = true;
            if (!raf) raf = requestAnimationFrame(pass);
        };
        const offs = [
            followActiveViewport((viewport) => {
                stale();
                return viewport.onCameraChange(stale);
            }),
            subscribeSelection(stale),
            useSelectedObjectStore.subscribe(stale),
            useVisibilityStore.subscribe(stale),
            useFeaAnimationStore.subscribe((s, p) => {
                if (
                    s.factor !== p.factor || s.scaleFactor !== p.scaleFactor || s.stepIndex !== p.stepIndex ||
                    s.mesh !== p.mesh || s.resultLayer !== p.resultLayer
                ) stale();
            }),
        ];
        stale();
        return () => {
            cancelAnimationFrame(raf);
            for (const off of offs) off();
        };
    }, [nodes, elements, values, sessionActive, tableFor, valuesFor]);

    if (!labels.length) return null;
    return (
        <div className="pointer-events-none absolute inset-0 overflow-hidden" data-number-labels="">
            {labels.map((l) => (
                <span
                    key={l.key}
                    data-label-kind={l.kind}
                    className={
                        "absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-sm px-0.5 font-mono text-[10px] leading-tight " +
                        (l.kind === "node"
                            ? "bg-sky-900/80 text-sky-100"
                            : l.kind === "value"
                              ? "bg-emerald-900/80 text-emerald-100"
                              : "bg-amber-900/80 text-amber-100")
                    }
                    style={{left: l.x, top: l.y}}
                >
                    {l.text}
                </span>
            ))}
        </div>
    );
}
