// The Measure tab's tool at work: while one is in hand it takes the scene's clicks
// (viewport/api/sceneClicks.ts) on whichever viewport is on screen -- a click then selects nothing
// and is no undo step; drags still move the camera -- snaps each click and the pointer's hover to
// the geometry's corners and edges (./snap.ts), and adds the points to the measurement being made.
// Escape ends that measurement (a path is kept, anything unfinished dropped), a second Escape puts
// the tool down; Enter finishes a path. Putting the tool down, or leaving the tab, gives the clicks
// back. No three.js.
import {modelPoint} from "@/plugins/scenePicks";
import {
    addMeasurePoint,
    endPendingMeasurement,
    setMeasureHover,
    setMeasureTool,
    useMeasureStore,
    type MeasurePoint,
    type MeasureTool,
} from "@/state/measureStore";
import {useSceneFrameStore} from "@/state/sceneFrameStore";
import {takeSceneClicks, type SceneClick, type SceneClickSession} from "@/viewport/api/sceneClicks";
import {VIEWPORT_INPUT_ATTR} from "@/viewport/input";
import {activeViewport, onActiveViewportChange, type ObjectRef, type ViewportApi} from "@/viewport/api/viewportApi";
import {bindMeasureDrawing} from "./measureDrawing";
import {featureGraph, snapPoint, type FeatureGraph, type Projector, type SnapResult} from "./snap";
import type {Vec3} from "./measureMath";

export const MEASURE_OWNER = "core:measure";

// ----- snapping a point of the view -----------------------------------------------------------------

/** Each object's feature graph, found once per viewport; a few. */
const graphs = new Map<string, FeatureGraph | null>();
let graphsOf: ViewportApi | null = null;
const KEEP_GRAPHS = 32;
/** The object the pointer was last over: its edges are still snapped to just off its silhouette. */
let lastRef: ObjectRef | null = null;

const refKey = (ref: ObjectRef) => `${ref.model}\u0000${ref.id}`;

function graphOf(viewport: ViewportApi, ref: ObjectRef): FeatureGraph | null {
    if (graphsOf !== viewport) {
        graphs.clear();
        graphsOf = viewport;
    }
    const key = refKey(ref);
    if (graphs.has(key)) return graphs.get(key) ?? null;
    const edges = viewport.featureEdges?.(ref) ?? null;
    const graph = edges && edges.length ? featureGraph(edges) : null;
    if (graphs.size >= KEEP_GRAPHS) graphs.delete(graphs.keys().next().value!);
    graphs.set(key, graph);
    return graph;
}

/** World points onto the viewport's screen (CSS pixels from its corner), null behind the camera. */
export function viewportProjector(viewport: ViewportApi): Projector {
    const world = new Float32Array(3);
    const out = new Float32Array(3);
    return (p) => {
        world[0] = p[0];
        world[1] = p[1];
        world[2] = p[2];
        viewport.projectToScreen(world, 1, out);
        return Number.isFinite(out[0]) && Number.isFinite(out[1]) ? [out[0], out[1]] : null;
    };
}

/** What a click at (x, y) of the view would measure on `viewport`: the surface under it snapped to
 *  the corners and edges of the object it is on (and of the one the pointer was over before). */
export async function snapAt(viewport: ViewportApi, x: number, y: number, snap = true): Promise<SnapResult | null> {
    const hit = (await viewport.surfaceAt?.(x, y)) ?? null;
    const near: FeatureGraph[] = [];
    if (snap) {
        const refs = [hit?.ref ?? null, lastRef].filter((r): r is ObjectRef => !!r);
        const seen = new Set<string>();
        for (const ref of refs) {
            if (seen.has(refKey(ref))) continue;
            seen.add(refKey(ref));
            const g = graphOf(viewport, ref);
            if (g) near.push(g);
        }
    }
    if (hit?.ref) lastRef = hit.ref;
    return snapPoint([x, y], hit?.world ?? null, near, viewportProjector(viewport), {eye: viewport.cameraPose()?.eye ?? null, snap});
}

/** A snapped point as the store keeps it: in the world, and in the models' own coordinates. */
export function measurePoint(snapped: SnapResult, offset: readonly number[] | null = useSceneFrameStore.getState().offset): MeasurePoint {
    const world = snapped.world;
    const model = modelPoint(world, offset ?? [0, 0, 0]) as Vec3;
    return {world, model, kind: snapped.kind, ...(snapped.edge ? {edge: snapped.edge} : {})};
}

// ----- holding the clicks ----------------------------------------------------------------------------

/** The pointer's hover is snapped at most this often. */
const HOVER_MS = 40;

let session: SceneClickSession | null = null;
let releaseInput: (() => void) | null = null;

/** A click the tool took: snapped, then added. Async work after the click is taken: the viewport has
 *  already been told the click is the tool's. */
function onClick(click: SceneClick): boolean {
    const viewport = activeViewport();
    if (!viewport?.surfaceAt) return false;
    void (async () => {
        const snapped = await snapAt(viewport, click.x, click.y, useMeasureStore.getState().snap);
        if (!snapped || !useMeasureStore.getState().tool) return;
        addMeasurePoint(measurePoint(snapped));
        setMeasureHover(snapped);
    })();
    return true;
}

/** The pointer over the view: what it would snap to, asked one at a time (the latest position waits
 *  for the answer before). Not while a button is held: that is a drag of the camera. */
function bindHover(): () => void {
    let busy = false;
    let next: {x: number; y: number} | null = null;
    let lastAsked = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const ask = (x: number, y: number) => {
        const viewport = activeViewport();
        if (!viewport?.surfaceAt) return;
        // A pick is a pass over the scene on the GPU: at most one per HOVER_MS, however fast the
        // pointer reports.
        const wait = lastAsked + HOVER_MS - performance.now();
        if (wait > 0) {
            next = {x, y};
            timer ??= setTimeout(() => {
                timer = null;
                if (!next || busy) return;
                const {x: nx, y: ny} = next;
                next = null;
                ask(nx, ny);
            }, wait);
            return;
        }
        lastAsked = performance.now();
        busy = true;
        void snapAt(viewport, x, y, useMeasureStore.getState().snap)
            .then((s) => {
                if (useMeasureStore.getState().tool) setMeasureHover(s);
            })
            .catch(() => setMeasureHover(null))
            .finally(() => {
                busy = false;
                if (next) {
                    const {x: nx, y: ny} = next;
                    next = null;
                    ask(nx, ny);
                }
            });
    };
    const onMove = (e: PointerEvent) => {
        const view = (e.target as Element | null)?.closest?.(`[${VIEWPORT_INPUT_ATTR}]`);
        if (!view) return;
        if (e.buttons !== 0) return void (next = null);
        const rect = view.getBoundingClientRect();
        const x = e.clientX - rect.left, y = e.clientY - rect.top;
        if (busy) next = {x, y};
        else ask(x, y);
    };
    const onLeave = (e: PointerEvent) => {
        if ((e.target as Element | null)?.closest?.(`[${VIEWPORT_INPUT_ATTR}]`)) setMeasureHover(null);
    };
    const onKey = (e: KeyboardEvent) => {
        const target = e.target as HTMLElement | null;
        if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
        if (e.key === "Escape") {
            // The measurement being made ends; with none, the tool is put down.
            if (!endPendingMeasurement()) disarmMeasureTool();
        } else if (e.key === "Enter" && useMeasureStore.getState().tool === "path") {
            if (!endPendingMeasurement()) return;
        } else {
            return;
        }
        e.preventDefault();
        e.stopPropagation();
    };
    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerout", onLeave, true);
    window.addEventListener("keydown", onKey, true);
    // Another viewport on screen: what the old one snapped to means nothing there.
    const offViewport = onActiveViewportChange(() => {
        lastRef = null;
        setMeasureHover(null);
    });
    return () => {
        if (timer) clearTimeout(timer);
        timer = null;
        window.removeEventListener("pointermove", onMove, true);
        window.removeEventListener("pointerout", onLeave, true);
        window.removeEventListener("keydown", onKey, true);
        offViewport();
    };
}

/** Take `tool` up: the scene's clicks are the tool's until it is put down. */
export function armMeasureTool(tool: MeasureTool): void {
    bindMeasureDrawing();
    setMeasureTool(tool);
    session ??= takeSceneClicks(MEASURE_OWNER, onClick);
    if (!releaseInput && typeof window !== "undefined") releaseInput = bindHover();
}

/** Put the tool down: the clicks go back to the viewport, the measurement being made is dropped. The
 *  measurements made stay. */
export function disarmMeasureTool(): void {
    session?.end();
    session = null;
    releaseInput?.();
    releaseInput = null;
    lastRef = null;
    if (useMeasureStore.getState().tool !== null) setMeasureTool(null);
}

/** Whether a tool holds the scene's clicks now. */
export function measureToolArmed(): boolean {
    return session !== null;
}
