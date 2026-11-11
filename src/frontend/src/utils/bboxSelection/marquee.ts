/**
 * The selection rectangle drawn on the screen while it is dragged: one element over the view. Shared
 * by both viewports; no three.js.
 */

import {screenBoxMode} from "./screenRect";

/** The selection rectangle's element, over the canvas and transparent to the pointer. */
export function marqueeElement(container: HTMLElement): HTMLDivElement {
    const el = document.createElement("div");
    el.style.cssText = "position:absolute;pointer-events:none;z-index:30;box-sizing:border-box;";
    container.appendChild(el);
    return el;
}

/** Place the rectangle between two client points; styled as CAD tools style it: a window solid
 *  blue, a crossing dashed green. */
export function drawMarquee(el: HTMLDivElement, container: HTMLElement, a: {x: number; y: number}, b: {x: number; y: number}): void {
    const r = container.getBoundingClientRect();
    const crossing = screenBoxMode(a.x, b.x) === "crossing";
    el.style.left = `${Math.min(a.x, b.x) - r.left}px`;
    el.style.top = `${Math.min(a.y, b.y) - r.top}px`;
    el.style.width = `${Math.abs(b.x - a.x)}px`;
    el.style.height = `${Math.abs(b.y - a.y)}px`;
    el.style.border = crossing ? "1px dashed rgb(74 222 128)" : "1px solid rgb(96 165 250)";
    el.style.background = crossing ? "rgb(74 222 128 / 0.08)" : "rgb(96 165 250 / 0.08)";
}
