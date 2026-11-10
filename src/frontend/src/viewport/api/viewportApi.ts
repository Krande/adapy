// What a viewport does for the rest of the viewer, whichever one is on screen: the three.js viewport or
// the three.js-free one. Commands, keys and panels talk to this interface instead of reaching into a
// THREE.Scene, so a feature written against it works on both, and the choice of viewport is one setting.
//
// The interface grows a feature area at a time as each is moved onto it. An area that is not here yet
// still goes straight to three.js and is simply absent on the three.js-free viewport. Nothing in this
// module (or anything it imports at runtime) may import three.js: the three.js-free viewport's chunk
// depends on it.
import {type CameraView} from "@/layout/coreCommandIds";

export type ViewportKind = "three" | "native";

/** Things one viewport can do and another cannot yet. A caller asks before offering it. */
export type ViewportFeature = "fit-selection";

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

export interface SetSelectionOptions {
    /** Add to the selection instead of replacing it. */
    add?: boolean;
    /** Runs as part of the same step: state that belongs to the selection (what the object info
     *  names), so an undo takes both back. */
    within?: () => void;
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
    /** Returns how many objects it selected. */
    setSelection(refs: readonly ObjectRef[], options?: SetSelectionOptions): number;
    clearSelection(): void;
    hideSelected(): void;
    /** Hide everything that is not selected. */
    hideOthers(): void;
    showAll(): void;
    /** Hide `refs` (one undo step where the viewport has undo); the selection is cleared, as its
     *  highlight would keep drawing what was hidden. */
    hide(refs: readonly ObjectRef[]): void;
    show(refs: readonly ObjectRef[]): void;
    /** Show `refs` and hide everything else. */
    isolate(refs: readonly ObjectRef[]): void;
    objectVisibility(): ObjectVisibilityReader;

    /** The loaded model's box (min xyz, max xyz), or null with nothing loaded. */
    modelBounds(): [number, number, number, number, number, number] | null;

    // The camera.
    fitAll(): void;
    fitSelection(): void;
    setView(view: CameraView): void;
    resetCamera(): void;
    /** One keyboard step of orbit: `right` around the up axis, `up` over the top; negative the other way. */
    orbitStep(right: number, up: number): void;
    /** One keyboard step of pan in the plane of the screen. */
    panStep(right: number, up: number): void;
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

export const NO_VIEWPORT = "No 3D view";

/** A model a viewport loaded without building a three.js scene for it: what the panels need to list it. */
export interface LoadedModelInfo {
    model: string;
    sourceName: string;
    /** The GLB scene's extras, as JSON text ("" when it has none). */
    sceneExtras: string;
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
