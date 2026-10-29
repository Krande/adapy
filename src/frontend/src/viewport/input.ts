// The viewport's input surface: one transparent element over the viewer that receives every pointer,
// touch and wheel event, whichever renderer is drawing underneath. Camera controls, transform gizmos,
// pointer capture and cursors all go through it, so input never depends on a renderer's canvas being
// on top or hit-testable -- the three.js canvas and the adacpp WebGPU canvas both ignore input.
import type * as THREE from "three";

export const VIEWPORT_INPUT_ATTR = "data-viewport-input";

/** Create (once) and return the input surface of a viewer container. */
export function ensureViewportInput(container: HTMLElement): HTMLDivElement {
    let el = container.querySelector<HTMLDivElement>(`:scope > [${VIEWPORT_INPUT_ATTR}]`);
    if (!el) {
        el = document.createElement("div");
        el.setAttribute(VIEWPORT_INPUT_ATTR, "true");
        // Above the renderer canvases; overlays added later (gizmo, stats, labels) stay above it.
        Object.assign(el.style, {position: "absolute", inset: "0", touchAction: "none"});
        container.appendChild(el);
    }
    return el;
}

/** The input surface for the viewer that `renderer` draws in (its canvas when there is none). */
export function viewportInputElement(renderer: THREE.WebGLRenderer): HTMLElement {
    const canvas = renderer.domElement;
    return canvas.parentElement?.querySelector<HTMLElement>(`:scope > [${VIEWPORT_INPUT_ATTR}]`) ?? canvas;
}
