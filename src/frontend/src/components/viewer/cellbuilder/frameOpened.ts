import * as THREE from "three";
import {activeViewport} from "@/viewport/api/viewportApi";

/** Frame the camera on a newly opened model's cells, once per model: the builder's
 *  group is left out of "fit all" (it is an editing overlay), so a model opened into an
 *  empty scene would otherwise be looked at from wherever the camera was - often inside
 *  it. Returns the model now framed (null once none is open). A model whose cells are
 *  not drawn yet, or a viewport not ready to frame, waits. */
export function frameOpenedModel(cells: THREE.Object3D, modelId: string | null, framed: string | null): string | null {
    if (!modelId) return null; // closed: the next open frames again, the same model too
    if (modelId === framed) return framed;
    cells.updateWorldMatrix(true, true);
    const box = new THREE.Box3().setFromObject(cells);
    if (box.isEmpty()) return framed;
    const viewport = activeViewport();
    if (!viewport || !viewport.fitBox(box.min.toArray(), box.max.toArray())) return framed;
    return modelId;
}

/** Frames per open the framer keeps trying before it gives up (about two seconds). */
export const FRAME_ATTEMPTS = 120;

/**
 * `frameOpenedModel`, retried on the next animation frames until it lands: a model
 * opened by a link is open before the viewport can frame (the camera and its controls
 * come a few frames later), and nothing redraws the builder then to ask again.
 */
export function openedModelFramer(
    cells: THREE.Object3D,
    currentModel: () => string | null,
    raf: (cb: () => void) => number = requestAnimationFrame,
    cancel: (id: number) => void = cancelAnimationFrame,
): {check: () => void; dispose: () => void} {
    let framed: string | null = null;
    let pending = 0;
    let left = 0;
    const attempt = () => {
        pending = 0;
        const model = currentModel();
        framed = frameOpenedModel(cells, model, framed);
        if (model && framed !== model && left-- > 0) pending = raf(attempt);
    };
    return {
        check: () => {
            if (pending) cancel(pending);
            left = FRAME_ATTEMPTS;
            attempt();
        },
        dispose: () => {
            if (pending) cancel(pending);
            pending = 0;
        },
    };
}
