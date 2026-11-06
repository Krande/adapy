// The FEA result controls, in every window.
//
// The Simulation panel (and core's playback toolbar) drive a loaded result through
// the FEA animation store and the scene code behind it. A pop-out window has no
// scene, so the panel works there through two halves of one seam:
//
//   * `core:fea-controls` (`feaControlsTopic`) -- the result session's state as
//     plain data: the manifest, the shown field / component / step, the transport
//     (playing, scale, period), the visualisation toggles, the colour scale and
//     the edge fade. The main window publishes it; a pop-out puts it into its own
//     copy of the stores, so the panel renders as it does in the main window.
//   * `feaControlActions()` -- what the controls DO. In the main window each action
//     runs here (`localFeaControlActions`, reusing `./playbackActions`, the
//     loader and `./resultSelection`); in a pop-out it is sent to the main window
//     as a mirror command (`FEA_CONTROL_COMMANDS`), which runs the same action.
//
// Commands carry one value (a number, a string, a boolean, a small object),
// checked before it is used. The topic's manifest is sent when it changes (a
// request always gets everything); the high-rate fields (the factor and the step
// while playing) are throttled to `PLAYING_PUBLISH_MS`.
//
// Light on purpose (stores and pure code): the scene code is imported when an
// action runs.

import {useMemo} from "react";

import {isMirroredWindow} from "@/layout/sceneActions";
import type {WindowBus} from "@/layout/windowBus";
import {registerMirrorCommand, registerMirrorTopic, sendMirrorCommand, type MirrorTopic} from "@/layout/windowMirror";
import {windowBus} from "@/layout/windows";
import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore, type FeaAnimationState} from "@/state/feaAnimationStore";
import {requestRender, usePerfStore} from "@/state/perfStore";
import type {ContourSettings} from "@/utils/scene/fea/contourScale";

export const FEA_CONTROLS_TOPIC = "core:fea-controls";

/** The fields of the FEA animation store a pop-out mirrors; `manifest` is sent when it changes. */
const PLAIN_FIELDS = [
    "sessionActive",
    "range",
    "factor",
    "period",
    "isPlaying",
    "stepIndex",
    "nSteps",
    "sourceName",
    "fieldName",
    "reduction",
    "colormap",
    "contour",
    "warpEnabled",
    "scaleFactor",
    "showUndeformed",
    "elementEdgesVisible",
    "resultColorsVisible",
    "layer",
    "ipReduction",
    "nodalAverage",
    "beamSolidsVisible",
    "beamSolidEdgesVisible",
    "resultPointMarkersVisible",
    "beamSolidsUnavailable",
    "framePreload",
    "timeHistory",
    "legendScope",
    "legendScopeLabel",
    "autoRange",
    "envelopeMode",
] as const satisfies readonly (keyof FeaAnimationState)[];

/** What changes on every frame while playing: throttled. */
const FAST_FIELDS: readonly string[] = ["factor", "stepIndex", "framePreload"];

/** Longest between two messages of the playing fields. */
export const PLAYING_PUBLISH_MS = 100;

type PlainKey = (typeof PLAIN_FIELDS)[number];
type Plain = Pick<FeaAnimationState, PlainKey>;

/** The result session's state, as a pop-out's controls read it. */
export interface FeaControlsSnapshot extends Plain {
    /** Sent when it changes (and to a window that asks); absent from a message that has no news of it. */
    manifest?: FeaAnimationState["manifest"];
    /** The colour legend's range (the colour scale dialog previews it). */
    legend: {min: number; max: number};
    /** The element grid's zoom fade (a render setting of the main window's). */
    edgeFade: {on: boolean; fullPx: number};
}

/** The edge fade of the main window, as this window's controls show it (a pop-out's copy). */
let mirroredEdgeFade: {on: boolean; fullPx: number} | null = null;
const edgeFadeListeners = new Set<() => void>();

/** The element grid's fade: this window's setting in the main window, the main window's in a pop-out. */
export function currentEdgeFade(): {on: boolean; fullPx: number} {
    if (isMirroredWindow() && mirroredEdgeFade) return mirroredEdgeFade;
    const p = usePerfStore.getState();
    return {on: p.edgeFade, fullPx: p.edgeFadeFullPx};
}

/** Subscribe to `currentEdgeFade` (a pop-out's mirrored copy and the perf store). */
export function subscribeEdgeFade(cb: () => void): () => void {
    edgeFadeListeners.add(cb);
    const off = usePerfStore.subscribe((s, p) => void ((s.edgeFade !== p.edgeFade || s.edgeFadeFullPx !== p.edgeFadeFullPx) && cb()));
    return () => {
        edgeFadeListeners.delete(cb);
        off();
    };
}

let edgeFadeSnap: {on: boolean; fullPx: number} = {on: false, fullPx: 0};
/** `currentEdgeFade()` with a stable identity while it does not change (for `useSyncExternalStore`). */
export function edgeFadeSnapshot(): {on: boolean; fullPx: number} {
    const now = currentEdgeFade();
    if (edgeFadeSnap.on !== now.on || edgeFadeSnap.fullPx !== now.fullPx) edgeFadeSnap = {on: now.on, fullPx: now.fullPx};
    return edgeFadeSnap;
}

export function feaControlsSnapshot(withManifest = true): FeaControlsSnapshot {
    const s = useFeaAnimationStore.getState();
    const out: Record<string, unknown> = {};
    for (const k of PLAIN_FIELDS) out[k] = s[k];
    if (withManifest) out.manifest = s.manifest;
    const c = useColorStore.getState();
    const p = usePerfStore.getState();
    out.legend = {min: c.min, max: c.max};
    out.edgeFade = {on: p.edgeFade, fullPx: p.edgeFadeFullPx};
    return out as unknown as FeaControlsSnapshot;
}

/** Put a snapshot into this window's stores. False: a message without a manifest before one arrived. */
export function applyFeaControls(data: FeaControlsSnapshot): boolean {
    if (!data || typeof data !== "object") return false;
    const hasManifest = "manifest" in data;
    if (!hasManifest && !gotManifest) return false;
    const {manifest, legend, edgeFade, ...plain} = data;
    useFeaAnimationStore.setState(hasManifest ? {...plain, manifest: manifest ?? null} : plain);
    if (hasManifest) gotManifest = true;
    if (legend) useColorStore.setState({min: legend.min, max: legend.max});
    if (edgeFade && (mirroredEdgeFade?.on !== edgeFade.on || mirroredEdgeFade?.fullPx !== edgeFade.fullPx)) {
        mirroredEdgeFade = edgeFade;
        for (const l of [...edgeFadeListeners]) l();
    }
    return true;
}

let gotManifest = false;

/** The topic. The playing fields are throttled with `later`. */
export function feaControlsTopic(later: (fn: () => void, ms: number) => void = (fn, ms) => void setTimeout(fn, ms)): MirrorTopic<FeaControlsSnapshot> {
    let sentManifest: unknown = undefined;
    return {
        id: FEA_CONTROLS_TOPIC,
        snapshot: () => {
            sentManifest = useFeaAnimationStore.getState().manifest;
            return feaControlsSnapshot(true);
        },
        delta: () => {
            const manifest = useFeaAnimationStore.getState().manifest;
            if (manifest === sentManifest) return feaControlsSnapshot(false);
            sentManifest = manifest;
            return feaControlsSnapshot(true);
        },
        apply: applyFeaControls,
        subscribe(cb) {
            let timer = false;
            const changed = (s: Record<string, unknown>, p: Record<string, unknown>, keys: readonly string[]) => keys.some((k) => s[k] !== p[k]);
            const slow = PLAIN_FIELDS.filter((k) => !FAST_FIELDS.includes(k));
            const offFea = useFeaAnimationStore.subscribe((s, p) => {
                const a = s as unknown as Record<string, unknown>;
                const b = p as unknown as Record<string, unknown>;
                const slowChange = a.manifest !== b.manifest || changed(a, b, slow);
                if (!slowChange && !changed(a, b, FAST_FIELDS)) return;
                if (slowChange || !s.isPlaying) {
                    cb();
                    return;
                }
                // Playing moves these on every frame: at most one message per interval.
                if (timer) return;
                timer = true;
                later(() => {
                    timer = false;
                    cb();
                }, PLAYING_PUBLISH_MS);
            });
            const offColor = useColorStore.subscribe((s, p) => void ((s.min !== p.min || s.max !== p.max) && cb()));
            const offPerf = usePerfStore.subscribe((s, p) => void ((s.edgeFade !== p.edgeFade || s.edgeFadeFullPx !== p.edgeFadeFullPx) && cb()));
            return () => {
                offFea();
                offColor();
                offPerf();
            };
        },
    };
}

// --- the actions ---

/** What the result controls do. */
export interface FeaControlActions {
    play(): void;
    pause(): void;
    stop(): void;
    /** Step forward or back by `delta`, wrapping. */
    step(delta: number): void;
    setStep(index: number): void;
    /** The paused deformation factor (a static field's slider). */
    setFactor(factor: number): void;
    /** The deformation scale. */
    setScale(scale: number): void;
    setPeriod(seconds: number): void;
    /** Show a field (its canonical name). */
    selectField(name: string): void;
    setReduction(reduction: string): void;
    setLayer(layer: string): void;
    setIpReduction(reduction: string): void;
    setColormap(name: string): void;
    setWarp(on: boolean): void;
    setNodalAverage(on: boolean): void;
    setEnvelope(on: boolean): void;
    setResultColors(on: boolean): void;
    setUndeformed(on: boolean): void;
    setElementEdges(on: boolean): void;
    setBeamSolids(on: boolean): void;
    setBeamSolidEdges(on: boolean): void;
    setResultPointMarkers(on: boolean): void;
    setEdgeFade(fade: {on: boolean; fullPx: number}): void;
    /** The colour scale's bounds and bands (an absent key stays); repaints. */
    setContour(next: Partial<ContourSettings>): void;
    resetContourRange(): void;
}

type ValueKind = "none" | "number" | "string" | "boolean" | "edgeFade" | "contour";

/** Each action's mirror command and the kind of value it carries. */
export const FEA_CONTROL_COMMANDS: Readonly<Record<keyof FeaControlActions, readonly [command: string, kind: ValueKind]>> = {
    play: ["core:fea-play", "none"],
    pause: ["core:fea-pause", "none"],
    stop: ["core:fea-stop", "none"],
    step: ["core:fea-step", "number"],
    setStep: ["core:fea-set-step", "number"],
    setFactor: ["core:fea-set-factor", "number"],
    setScale: ["core:fea-set-scale", "number"],
    setPeriod: ["core:fea-set-period", "number"],
    selectField: ["core:fea-select-field", "string"],
    setReduction: ["core:fea-set-reduction", "string"],
    setLayer: ["core:fea-set-layer", "string"],
    setIpReduction: ["core:fea-set-ip-reduction", "string"],
    setColormap: ["core:fea-set-colormap", "string"],
    setWarp: ["core:fea-set-warp", "boolean"],
    setNodalAverage: ["core:fea-set-nodal-average", "boolean"],
    setEnvelope: ["core:fea-set-envelope", "boolean"],
    setResultColors: ["core:fea-set-result-colors", "boolean"],
    setUndeformed: ["core:fea-set-undeformed", "boolean"],
    setElementEdges: ["core:fea-set-element-edges", "boolean"],
    setBeamSolids: ["core:fea-set-beam-solids", "boolean"],
    setBeamSolidEdges: ["core:fea-set-beam-solid-edges", "boolean"],
    setResultPointMarkers: ["core:fea-set-result-points", "boolean"],
    setEdgeFade: ["core:fea-set-edge-fade", "edgeFade"],
    setContour: ["core:fea-set-contour", "contour"],
    resetContourRange: ["core:fea-reset-contour", "none"],
};

const store = () => useFeaAnimationStore.getState();
const playback = () => import("./playbackActions");
const streaming = () => import("@/utils/scene/handlers/load_fea_streaming");
const selection = () => import("./resultSelection");

/** Repaint the shown (field, step) with the store's state, optionally with a reduction or colormap of its own. */
function reload(over: {reduction?: string; colormap?: string} = {}): void {
    const s = store();
    if (!s.sourceName || !s.manifest || !s.fieldName) return;
    const {sourceName, manifest, fieldName, stepIndex} = s;
    void streaming().then((m) =>
        m.load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex,
            reduction: over.reduction ?? s.reduction,
            displacementScale: s.factor * s.scaleFactor,
            ...(over.colormap !== undefined ? {colormap: over.colormap} : {}),
        }),
    );
}

const logged = (p: Promise<unknown>) => void p.catch((err) => console.error("result control failed", err));

/** The actions, run on this window's scene. */
export const localFeaControlActions: FeaControlActions = {
    play: () => void playback().then((m) => m.playResults()),
    pause: () => void playback().then((m) => m.pauseResults()),
    stop: () => void playback().then((m) => m.stopResults()),
    step: (delta) => void playback().then((m) => m.stepResults(delta)),
    setStep: (index) => void playback().then((m) => m.setResultStep(index)),
    setFactor: (factor) => void playback().then((m) => m.setResultFactor(factor)),
    setScale: (scale) => void playback().then((m) => m.setResultScale(scale)),
    setPeriod: (seconds) => {
        if (isFinite(seconds)) store().setPeriod(seconds);
    },
    selectField: (name) => logged(selection().then((m) => m.selectFeaResultComponent(name))),
    setReduction: (reduction) => reload({reduction}),
    setLayer: (layer) => logged(selection().then((m) => m.selectFeaResultLayer(layer))),
    setIpReduction: (reduction) => {
        store().setIpReduction(reduction);
        reload({colormap: store().colormap});
    },
    setColormap: (name) => {
        // The store first: the step closure reads the colormap off it when it runs.
        store().setColormap(name);
        reload({colormap: name});
    },
    setWarp: (on) => {
        store().setWarpEnabled(on);
        reload();
    },
    setNodalAverage: (on) => {
        store().setNodalAverage(on);
        reload({colormap: store().colormap});
    },
    setEnvelope: (on) => {
        store().setEnvelopeMode(on);
        reload({colormap: store().colormap});
    },
    setResultColors: (on) => {
        store().setResultColorsVisible(on);
        void streaming().then((m) => m.setFeaResultColorsVisible(on));
    },
    setUndeformed: (on) => store().setShowUndeformed(on),
    setElementEdges: (on) => store().setElementEdgesVisible(on),
    setBeamSolids: (on) => {
        store().setBeamSolidsVisible(on);
        void streaming().then((m) => m.setBeamSolidsVisible(on));
    },
    setBeamSolidEdges: (on) => {
        store().setBeamSolidEdgesVisible(on);
        void streaming().then((m) => m.syncFeaOverlayVisibility());
    },
    setResultPointMarkers: (on) => void streaming().then((m) => m.setFeaResultPointMarkersVisible(on)),
    setEdgeFade: ({on, fullPx}) => {
        const perf = usePerfStore.getState();
        perf.setEdgeFade(on);
        perf.setEdgeFadeFullPx(fullPx);
        requestRender();
    },
    setContour: (next) => logged(selection().then((m) => m.applyContourSettings(next))),
    resetContourRange: () => logged(selection().then((m) => m.resetContourBounds())),
};

/** The actions, sent to the main window over `bus`. */
export function remoteFeaControlActions(bus: WindowBus = windowBus()): FeaControlActions {
    const out: Record<string, (value?: unknown) => void> = {};
    for (const [name, [command, kind]] of Object.entries(FEA_CONTROL_COMMANDS)) {
        out[name] = kind === "none" ? () => sendMirrorCommand(bus, command) : (value) => sendMirrorCommand(bus, command, {value});
    }
    return out as unknown as FeaControlActions;
}

/** This window's actions: run here in the main window, sent there from a pop-out. */
export function feaControlActions(): FeaControlActions {
    return isMirroredWindow() ? remoteFeaControlActions() : localFeaControlActions;
}

/** `feaControlActions()` for a component. */
export function useFeaControlActions(): FeaControlActions {
    return useMemo(feaControlActions, []);
}

// --- the commands (main window) ---

/** A command's value if it is of `kind`, else undefined. */
export function checkedValue(kind: ValueKind, raw: unknown): unknown {
    switch (kind) {
        case "none":
            return null;
        case "number":
            return typeof raw === "number" && isFinite(raw) ? raw : undefined;
        case "string":
            return typeof raw === "string" && raw.length > 0 && raw.length < 500 ? raw : undefined;
        case "boolean":
            return typeof raw === "boolean" ? raw : undefined;
        case "edgeFade": {
            const v = raw as {on?: unknown; fullPx?: unknown} | null;
            return v && typeof v.on === "boolean" && typeof v.fullPx === "number" && isFinite(v.fullPx) ? {on: v.on, fullPx: v.fullPx} : undefined;
        }
        case "contour": {
            const v = raw as Record<string, unknown> | null;
            if (!v || typeof v !== "object") return undefined;
            const out: Partial<ContourSettings> = {};
            for (const k of ["min", "max", "levels"] as const) {
                if (!(k in v)) continue;
                const x = v[k];
                if (x !== null && !(typeof x === "number" && isFinite(x))) return undefined;
                out[k] = x as number | null;
            }
            return out;
        }
    }
}

let registered = false;

/** Register the topic and the commands (once per page). The commands run only in the main window. */
export function installFeaControls(): void {
    if (registered) return;
    registered = true;
    registerMirrorTopic(feaControlsTopic());
    // Read at each run, not bound now: one action object, whatever replaced a method of it.
    const a = localFeaControlActions as unknown as Record<string, (v?: unknown) => void>;
    for (const [name, [command, kind]] of Object.entries(FEA_CONTROL_COMMANDS)) {
        registerMirrorCommand<{value?: unknown}>({
            id: command,
            run: (args) => {
                const v = checkedValue(kind, args?.value);
                if (v === undefined) return;
                if (kind === "none") a[name]();
                else a[name](v);
            },
        });
    }
}

/** Forget the registration -- tests only (with `resetMirror`). */
export function resetFeaControlsForTests(): void {
    registered = false;
    gotManifest = false;
    mirroredEdgeFade = null;
    edgeFadeSnap = {on: false, fullPx: 0};
}
