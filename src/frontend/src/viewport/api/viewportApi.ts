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

export interface ViewportApi {
    readonly kind: ViewportKind;
    /** Why this viewport cannot do `feature`, in words for the user; null when it can. */
    unavailable(feature: ViewportFeature): string | null;

    // Selection and visibility, of the model's objects.
    selectionCount(): number;
    clearSelection(): void;
    hideSelected(): void;
    /** Hide everything that is not selected. */
    hideOthers(): void;
    showAll(): void;

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

export const NO_VIEWPORT = "No 3D view";
