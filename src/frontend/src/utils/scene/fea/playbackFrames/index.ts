// Recorded playback frames: the facade the loader and the playback driver use.
//
// One recording at a time, for one (source, field, view settings, renderer) key.
// Everything renderer-specific sits behind `FrameDisplacementBackend`; this module
// picks the adapter for the active renderer and is the only place that does.

import type * as THREE from "three";

import {usePerfStore, type RenderBackend} from "@/state/perfStore";
import type {FrameDisplacementBackend} from "./backend";
import {FrameRecorder} from "./recorder";
import {THREE_MAX_FRAMES, ThreeFrameBackend} from "./threeBackend";
import {WebGpuFrameBackend} from "./webgpuBackend";

export type {FrameDisplacementBackend} from "./backend";
export {FrameRecorder, sourceEdges} from "./recorder";

/** The adapter for `kind`, and how many frames it can hold at most. */
export function frameBackendFor(kind: RenderBackend): {backend: FrameDisplacementBackend; maxFrames: number} {
    switch (kind) {
        case "adacpp-webgpu":
            return {backend: new WebGpuFrameBackend(), maxFrames: Number.MAX_SAFE_INTEGER};
        case "three":
        default:
            return {backend: new ThreeFrameBackend(), maxFrames: THREE_MAX_FRAMES};
    }
}

/** The renderer drawing the viewport now. */
export function activeRenderBackend(): RenderBackend {
    return usePerfStore.getState().renderBackend;
}

let active: FrameRecorder | null = null;

export function activeFrameRecorder(): FrameRecorder | null {
    return active;
}

/**
 * The recording for `key` (which names the renderer too), made for these meshes if
 * there is none or the one there is was for something else. Null when not a single
 * frame fits the budget.
 */
export function frameRecorderFor(
    key: string,
    mesh: THREE.Mesh,
    nSource: number,
    beam: THREE.Mesh | null,
    nFrames: number,
    kind: RenderBackend = activeRenderBackend(),
): FrameRecorder | null {
    const fullKey = `${kind}\u0000${key}`;
    const nRender = mesh.geometry.getAttribute("position")?.count ?? 0;
    const beamVerts = beam?.geometry.getAttribute("position")?.count ?? 0;
    if (
        active
        && active.key === fullKey
        && active.nSource === nSource
        && active.nRender === nRender
        && active.beamVerts === beamVerts
    ) return active;
    disposeFrameRecorder();
    const {backend, maxFrames} = frameBackendFor(kind);
    const recorder = new FrameRecorder(fullKey, backend, nSource, nRender, beamVerts, nFrames, maxFrames);
    if (recorder.capacity === 0) {
        recorder.dispose();
        return null;
    }
    active = recorder;
    return active;
}

/** The recording, if it is the one for `key` under the active renderer. */
export function matchingFrameRecorder(key: string, kind: RenderBackend = activeRenderBackend()): FrameRecorder | null {
    return active && active.key === `${kind}\u0000${key}` ? active : null;
}

/** Stop showing recorded frames: an ordinary paint is about to land. */
export function releaseFrames(): void {
    active?.release();
}

/** Drop the recording (a new model, a new field, other view settings). */
export function disposeFrameRecorder(): void {
    active?.dispose();
    active = null;
}
