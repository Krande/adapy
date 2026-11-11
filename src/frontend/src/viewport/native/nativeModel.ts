// What the three.js-free viewport shows, and whether it is up. The model loader hands a model's bytes
// here instead of building a three.js scene while that viewport is the one on screen. No three.js in
// this module: the loader and the options panel read it without pulling the viewport's chunk in.
import {create} from "zustand";

export type NativeViewportState = "off" | "starting" | "active" | "failed";

export interface NativeModel {
    bytes: Uint8Array;
    sourceName: string;
    /** What the model is called to a person, when its loader was told. */
    displayName?: string;
    /** Beside the models on screen ("overlay"), or instead of them. */
    placement: "replace" | "overlay";
    /** Bumped per load, so the same file loaded again is still a change. */
    seq: number;
}

interface NativeViewportStore {
    state: NativeViewportState;
    /** Why the viewport is not up: the three.js viewport is shown instead. */
    error: string | null;
    model: NativeModel | null;
}

export const useNativeViewport = create<NativeViewportStore>(() => ({state: "off", error: null, model: null}));

// On the document element too, for end-to-end tests (as the adacpp renderer's state is).
const rootData = typeof document !== "undefined" ? document.documentElement?.dataset : undefined;
if (rootData) {
    const publish = (s: NativeViewportStore) => {
        rootData.nativeViewport = s.state;
        // Why it is not up, when it is not: a test that needs it can say why it could not run.
        rootData.nativeViewportError = s.error ?? "";
    };
    publish(useNativeViewport.getState());
    useNativeViewport.subscribe(publish);
}

/** The native viewport is the one on screen (it is mounted and has not failed). */
export function isNativeViewportActive(): boolean {
    const state = useNativeViewport.getState().state;
    return state === "starting" || state === "active";
}

let seq = 0;

/** Fetch a model and hand it to the native viewport. The fetch finishes before this returns: a caller
 *  may revoke the URL right after. */
export async function showInNativeViewport(
    url: string,
    requestHeaders: Record<string, string> | undefined,
    sourceName: string,
    placement: "replace" | "overlay" = "replace",
    displayName?: string,
): Promise<void> {
    const response = await fetch(url, requestHeaders ? {headers: requestHeaders} : undefined);
    if (!response.ok) throw new Error(`loading the model failed: ${response.status} ${response.statusText}`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    useNativeViewport.setState({model: {bytes, sourceName, displayName, placement, seq: ++seq}});
}
