// Number labels over the 3D view: node ids and element ids of a loaded FEA
// result, as HTML in screen space (`utils/scene/fea/numberLabels` decides which
// are worth drawing). Mounted by the viewport overlay layer when a shell asks for
// it (`UiShellSpec.chrome.overlays.labels`), or mounted by a shell over its own
// view. The toggles are core commands (`core:labels-nodes`, `core:labels-elements`).
//
// A pass costs a bounded scan of the mesh, and runs only when something it
// depends on changed (camera, view size, selection, deformation, toggles), at
// most every 60 ms.

import React from "react";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useLabelsStore} from "@/state/labelsStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {collectLabels, type ScreenLabel} from "@/utils/scene/fea/numberLabels";
import {loadNodeLabelTable} from "@/utils/scene/fea/nodeLabelTable";

/** The most labels on screen, and the most elements examined per pass. */
export const LABEL_CAP = 400;
export const LABEL_SCAN_CAP = 4000;

const MIN_INTERVAL_MS = 60;

export function NumberLabels() {
    const nodes = useLabelsStore((s) => s.nodes);
    const elements = useLabelsStore((s) => s.elements);
    const sessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const [labels, setLabels] = React.useState<ScreenLabel[]>([]);
    const table = React.useRef<number[] | null>(null);
    const [tableFor, setTableFor] = React.useState<unknown>(null);

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
        if ((!nodes && !elements) || !sessionActive) {
            setLabels([]);
            return;
        }
        let raf = 0;
        let last = 0;
        let sig = "";
        const tick = (now: number) => {
            raf = requestAnimationFrame(tick);
            if (now - last < MIN_INTERVAL_MS) return;
            const rt = getViewerRuntime();
            const camera = rt.camera.current;
            const el = rt.renderer.current?.domElement;
            const fea = useFeaAnimationStore.getState();
            const mesh = fea.mesh;
            if (!camera || !el || !mesh) return;
            const width = el.clientWidth;
            const height = el.clientHeight;
            const selected = useSelectedObjectStore.getState().selectedObjects.get(mesh) ?? null;
            const next = [
                camera.matrixWorld.elements.join(","),
                camera.projectionMatrix.elements[0],
                camera.projectionMatrix.elements[5],
                width,
                height,
                fea.factor,
                fea.scaleFactor,
                fea.stepIndex,
                selected ? `${selected.size}:${[...selected].slice(0, 8).join(",")}` : "-",
                tableFor ? "t" : "-",
            ].join("|");
            if (next === sig) return;
            sig = next;
            last = now;
            const t = table.current;
            setLabels(
                collectLabels(mesh, camera, {
                    nodes,
                    elements,
                    cap: LABEL_CAP,
                    scanCap: LABEL_SCAN_CAP,
                    selected,
                    width,
                    height,
                    nodeText: t ? (i) => String(t[i] ?? i + 1) : undefined,
                }),
            );
        };
        raf = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(raf);
    }, [nodes, elements, sessionActive, tableFor]);

    if (!labels.length) return null;
    return (
        <div className="pointer-events-none absolute inset-0 overflow-hidden" data-number-labels="">
            {labels.map((l) => (
                <span
                    key={l.key}
                    data-label-kind={l.kind}
                    className={
                        "absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-sm px-0.5 font-mono text-[10px] leading-tight " +
                        (l.kind === "node" ? "bg-sky-900/80 text-sky-100" : "bg-amber-900/80 text-amber-100")
                    }
                    style={{left: l.x, top: l.y}}
                >
                    {l.text}
                </span>
            ))}
        </div>
    );
}
