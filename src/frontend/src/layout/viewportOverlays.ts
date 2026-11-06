// HUDs over the 3D view, at named anchors: a shell's own, and (in a composed
// shell that asks for it) core's colour legend and the plugin panels that name
// the `overlay` dock. Drawn by `./ViewportOverlays` inside the view, so they move
// with it when the grid docks panels around it.
//
// Dependency-free, so the facade root can export it.

import type React from "react";

export type ViewportAnchor =
  | "top-left"
  | "top-center"
  | "top-right"
  | "left-center"
  | "right-center"
  | "bottom-left"
  | "bottom-center"
  | "bottom-right";

export const VIEWPORT_ANCHORS: readonly ViewportAnchor[] = [
  "top-left",
  "top-center",
  "top-right",
  "left-center",
  "right-center",
  "bottom-left",
  "bottom-center",
  "bottom-right",
];

export interface ViewportOverlaySpec {
  id: string;
  anchor: ViewportAnchor;
  /** Order in its anchor's stack (from the corner outwards). Default 0. */
  order?: number;
  render(): React.ReactNode;
  /** Drawn only while this holds. */
  when?(): boolean;
}

const _overlays = new Map<string, ViewportOverlaySpec & { owner: string }>();
let version = 0;
const listeners = new Set<() => void>();
const changed = () => {
  version++;
  for (const l of listeners) l();
};

/** Add a HUD over the view (a later one with the same id replaces it). */
export function registerViewportOverlay(spec: ViewportOverlaySpec, owner: string): void {
  _overlays.set(spec.id, { ...spec, owner });
  changed();
}

export function unregisterViewportOverlay(id: string): void {
  if (_overlays.delete(id)) changed();
}

/** The HUDs at `anchor`, in order. */
export function overlaysAt(anchor: ViewportAnchor): (ViewportOverlaySpec & { owner: string })[] {
  return [..._overlays.values()]
    .filter((o) => o.anchor === anchor)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.id.localeCompare(b.id));
}

export function subscribeViewportOverlays(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function viewportOverlaysVersion(): number {
  return version;
}

/** Clear -- unit tests only. */
export function resetViewportOverlays(): void {
  _overlays.clear();
  changed();
}
