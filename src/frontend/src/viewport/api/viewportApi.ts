// What a viewport does for the rest of the viewer, whichever one is on screen: the three.js viewport or
// the three.js-free one. Commands, keys and panels talk to this interface instead of reaching into a
// THREE.Scene, so a feature written against it works on both, and the choice of viewport is one setting.
//
// The interface grows a feature area at a time as each is moved onto it. An area that is not here yet
// still goes straight to three.js and is simply absent on the three.js-free viewport. Nothing in this
// module (or anything it imports at runtime) may import three.js: the three.js-free viewport's chunk
// depends on it.
import type {ViewportResults} from "./resultLayer";
import {type CameraView} from "@/layout/coreCommandIds";
import type {BillboardLayer, Point3} from "./billboards";
import type {OverlayLayer} from "./overlayLayer";
import type {ImageLayer} from "./imageLayer";
import type {ViewportBoxSelection} from "./boxSelection";
import type {ViewportUndoState} from "./viewportState";

export type ViewportKind = "three" | "native";

/** Things one viewport can do and another cannot yet. A caller asks before offering it. */
export type ViewportFeature = "fit-selection";

/** The node animation of the models on screen (their parts moving over time), shown a moment at a
 *  time. What the playback clock (state/playbackClock.ts) plays as one of its tracks. */
export interface ModelAnimation {
    /** Seconds: the longest of the models' animations. */
    readonly duration: number;
    /** Put every animated part where it is at `t` seconds. Allocates nothing. */
    showTime(t: number): void;
}

/** One object of a loaded model: the model's key and the object's id in it (the GLB's own id for it,
 *  which a Scene tree row carries as `model_key` and `rangeId`). The identity panels use; neither
 *  viewport's own representation of the object appears outside it. */
export interface ObjectRef {
    model: string;
    id: string;
    /** The GLB node that draws it, when the caller already knows (a tree row does): it saves a
     *  viewport that works per node a lookup. Never required. */
    node?: string | null;
}

export interface ViewportCameraPose {
    eye: [number, number, number];
    target: [number, number, number];
    up: [number, number, number];
    fovDeg: number;
}

export interface SetSelectionOptions {
    /** Add to the selection instead of replacing it. */
    add?: boolean;
    /** Runs as part of the same step: state that belongs to the selection (what the object info
     *  names), so an undo takes both back. */
    within?: () => void;
    /** Selections made with the same key in quick succession are one undo step: what re-applies
     *  while something is dragged (a selection box's handles). */
    coalesce?: string;
}

/** Reads how one object is drawn now: "shown", "hidden", or null when the viewport draws nothing for
 *  it (a level of the hierarchy). Good for one pass over many objects; get a new one after a change. */
export type ObjectVisibilityReader = (model: string, id: string, node?: string | null) => "shown" | "hidden" | null;

export interface ViewportApi {
    readonly kind: ViewportKind;
    /** Why this viewport cannot do `feature`, in words for the user; null when it can. */
    unavailable(feature: ViewportFeature): string | null;

    // Selection and visibility, of the model's objects.
    selectionCount(): number;
    /** The selected objects. */
    selection(): ObjectRef[];
    /** A value that is a different one whenever the selection has changed. */
    selectionToken(): unknown;
    /** Called after each change of the selection. Returns the unsubscribe function. */
    onSelectionChange(listener: () => void): () => void;
    /** Make `refs` the selection (one undo step where the viewport has undo). Objects that are not
     *  drawn (a level of the hierarchy, an unknown id) are left out. */
    /** Returns how many objects it selected. `refs` is read once, in order, and no ref is kept: a
     *  caller selecting thousands of objects may yield one object over and over with new values. */
    setSelection(refs: Iterable<ObjectRef>, options?: SetSelectionOptions): number;
    clearSelection(): void;
    hideSelected(): void;
    /** Hide everything that is not selected. */
    hideOthers(): void;
    showAll(): void;
    /** Hide `refs` (one undo step where the viewport has undo); the selection is cleared, as its
     *  highlight would keep drawing what was hidden. */
    hide(refs: Iterable<ObjectRef>): void;
    show(refs: Iterable<ObjectRef>): void;
    /** Show `refs` and hide everything else. As with `setSelection`, `refs` is read once and no
     *  ref is kept. */
    isolate(refs: Iterable<ObjectRef>): void;
    objectVisibility(): ObjectVisibilityReader;
    // Undo: the selection and what is hidden as a state the history captures before an action and
    // puts back on undo and redo (./viewportState.ts). Each of the calls above that changes either is
    // one undo step.
    captureState: ViewportUndoState["captureState"];
    stateChange: ViewportUndoState["stateChange"];
    restoreState: ViewportUndoState["restoreState"];
    /** Where the origin of the model loaded from `sourceName` is in the world: the offset its
     *  loader moved it by (state/sceneFrameStore has the scene's one offset). Null when no such model
     *  is on screen. */
    sourceOrigin(sourceName: string): Point3 | null;

    /** The loaded model's box (min xyz, max xyz), or null with nothing loaded. */
    modelBounds(): [number, number, number, number, number, number] | null;
    /** Drawing an FEA result: meshes whose colours and displacement are replaced step by step
     *  (./resultLayer.ts). */
    readonly results: ViewportResults;

    /** Take the model loaded from `sourceName` off the screen, when this viewport holds its models
     *  itself; false when it does not (the three.js scene's models are unloaded with the scene). */
    unloadSource(sourceName: string): boolean;
    /** Save a picture of the view as it is now (a PNG download). */
    saveScreenshot(): void;

    // The camera.
    /** Where the camera is and what it looks at, for input that moves it directly (a 3D mouse).
     *  `up` is the world's up axis, not the camera's own; null before there is a camera. */
    cameraPose(): ViewportCameraPose | null;
    /** Put the camera at `eye` looking at `target`, rolled by `roll` radians about that direction. */
    setCameraPose(eye: Point3, target: Point3, roll: number): void;
    fitAll(): void;
    fitSelection(): void;
    setView(view: CameraView): void;
    resetCamera(): void;
    /** One keyboard step of orbit: `right` around the up axis, `up` over the top; negative the other way. */
    orbitStep(right: number, up: number): void;
    /** One keyboard step of pan in the plane of the screen. */
    panStep(right: number, up: number): void;

    // Labels, markers and icons over the model.
    /** A point in the middle of what `refs` draw, in world space: where a label or a marker for them
     *  goes. Null when none of them is drawn. */
    objectsCentre(refs: readonly ObjectRef[]): Point3 | null;
    /** The viewport's layer of camera-facing images called `name`, made on first use; asking again
     *  gives the same layer until it is disposed. */
    billboards(name: string): BillboardLayer;
    /** The viewport's layer of lines and points called `name` (./overlayLayer.ts), made on first
     *  use; asking again gives the same layer until it is disposed. */
    overlays(name: string): OverlayLayer;
    /** The viewport's layer of images lying in the world called `name` (./imageLayer.ts), made on
     *  first use; asking again gives the same layer until it is disposed. */
    images(name: string): ImageLayer;
    /** The node animation of the models on screen; null when none of them has one. The same object
     *  until the models change. */
    modelAnimation(): ModelAnimation | null;
    /** Orbit about `point` from now on (what a double-click on the model does). */
    setOrbitPivot(point: Point3): void;
    /** Frame the camera on a box in the world (min xyz, max xyz), keeping the direction it looks
     *  in. False when there is no camera yet or the box is empty. */
    fitBox(min: Point3, max: Point3): boolean;

    // Box selection: what a box in the world or a rectangle on the screen takes (./boxSelection.ts).
    objectsInBox: ViewportBoxSelection["objectsInBox"];
    objectsInScreenRect: ViewportBoxSelection["objectsInScreenRect"];
    selectionBounds: ViewportBoxSelection["selectionBounds"];
    // Screen positions, for what is drawn in HTML over the view (number labels).
    /** Where `count` world points (xyz each, in `world`) are in the view now: per point, x and y in
     *  CSS pixels from the view's top-left corner and the depth (0 at the near plane, 1 at the far
     *  one) into `out`, three numbers each; a point behind the camera gets NaN. Returns the view's
     *  size in CSS pixels. Nothing is allocated per point. */
    projectToScreen(world: Float32Array, count: number, out: Float32Array): {width: number; height: number};
    /** Called when the camera has moved or the view has changed size: what was projected is out
     *  of date. A viewport that draws only when something changed may call it for every frame it
     *  draws. Returns the unsubscribe function. */
    onCameraChange(listener: () => void): () => void;
}

let active: ViewportApi | null = null;
const listeners = new Set<() => void>();

/** The viewport on screen, or null when there is no 3D view. */
export function activeViewport(): ViewportApi | null {
    return active;
}

/** Make `viewport` the one on screen. Returns the function that withdraws it (when it unmounts); a
 *  viewport that has since been replaced withdraws nothing. */
export function setActiveViewport(viewport: ViewportApi): () => void {
    active = viewport;
    listeners.forEach((l) => l());
    return () => {
        if (active !== viewport) return;
        active = null;
        listeners.forEach((l) => l());
    };
}

/** Called whenever the viewport on screen changes. Returns the unsubscribe function. */
export function onActiveViewportChange(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
}

/** Called whenever the selection on screen may have changed: the viewport's selection did, or another
 *  viewport took its place. What a panel subscribes to (see `useViewportSelection`). */
export function subscribeSelection(listener: () => void): () => void {
    let off = active?.onSelectionChange(listener) ?? null;
    const offViewport = onActiveViewportChange(() => {
        off?.();
        off = active?.onSelectionChange(listener) ?? null;
        listener();
    });
    return () => {
        off?.();
        offViewport();
    };
}

/** Keep something attached to whichever viewport is on screen: `attach` runs for the one there now and
 *  for each that replaces it, and what it returns is called when that viewport leaves. Returns the
 *  function that detaches for good. */
export function followActiveViewport(attach: (viewport: ViewportApi) => () => void): () => void {
    let detach: (() => void) | null = null;
    let attached: ViewportApi | null = null;
    const follow = () => {
        if (active === attached) return;
        detach?.();
        attached = active;
        detach = active ? attach(active) : null;
    };
    follow();
    const off = onActiveViewportChange(follow);
    return () => {
        off();
        detach?.();
        detach = null;
    };
}

export const NO_VIEWPORT = "No 3D view";

/** A model a viewport loaded without building a three.js scene for it: what the panels need to list it. */
export interface LoadedModelInfo {
    /** True when it joined the models on screen instead of replacing them. */
    added?: boolean;
    model: string;
    sourceName: string;
    /** What the model is called to a person (its root row in the Scene tree), when its loader was
     *  told; the model's own root name or the source name otherwise. */
    displayName?: string;
    /** The GLB scene's extras, as JSON text ("" when it has none). */
    sceneExtras: string;
    /** The same extras as an object, from a viewport that made them itself instead of reading
     *  them from a file; `sceneExtras` is then "". */
    extras?: Record<string, unknown>;
    /** The root `ADA_EXT_data` extension, as JSON text ("" when it has none). */
    adaExtData?: string;
    /** Reads one of the file's buffer views (what the extension refers to by index); null when
     *  there is none. Good during the announcement only: a listener does not keep it. */
    bufferView?: (index: number) => Uint8Array | null;
}

/** A click in a viewport: the object under it, or null for empty space. */
export interface ViewportPick {
    ref: ObjectRef | null;
}

function channel<T>(): {on: (listener: (value: T) => void) => () => void; emit: (value: T) => void} {
    const subscribers = new Set<(value: T) => void>();
    return {
        on: (listener) => {
            subscribers.add(listener);
            return () => void subscribers.delete(listener);
        },
        emit: (value) => subscribers.forEach((l) => l(value)),
    };
}

const modelLoaded = channel<LoadedModelInfo>();
const picked = channel<ViewportPick>();
/** A viewport replaced what it shows with this model. The panels' side listens (the Scene tree). */
export const onModelLoaded = modelLoaded.on;
export const notifyModelLoaded = modelLoaded.emit;
/** A viewport's click picked an object (or nothing), after it updated its selection. */
export const onViewportPick = picked.on;
export const notifyViewportPick = picked.emit;

const animationChanged = channel<void>();
/** The models on screen changed in a way that may have changed `modelAnimation()`: one with an
 *  animation was loaded, or removed. */
export const onModelAnimationChange = animationChanged.on;
export const notifyModelAnimationChange = (): void => animationChanged.emit();
