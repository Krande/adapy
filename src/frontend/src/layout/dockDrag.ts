// Dragging a floating panel's header onto a zone to dock it there.
//
// A zone that takes a drop renders an element carrying `DROP_ATTR` (its zone
// id) while a drag is on. Every move hit-tests the pointer with
// `document.elementsFromPoint`, which sees through the dragged window to what is
// under it; the drop docks into the zone found there. Nothing is re-parented:
// docking is a document change, and React draws the panel in its new place.
//
// A vanilla zustand store, so the drop targets re-render while the window's own
// drag handlers stay plain functions.

import { createStore } from "zustand/vanilla";

import type { PanelId, ZoneId } from "./panel";

/** On a drop target: the zone a drop there docks into. */
export const DROP_ATTR = "data-layout-drop";

export interface DockDragState {
  /** The panel whose window is being dragged. */
  panel: PanelId | null;
  /** The zone under the pointer, if it takes the drop. */
  over: ZoneId | null;
}

export const dockDragStore = createStore<DockDragState>()(() => ({ panel: null, over: null }));

/** The minimal element surface the hit test reads. */
export interface DropCandidate {
  closest?(selector: string): { getAttribute(name: string): string | null } | null;
}

/** The zone of the first drop target among `elements` (topmost first, as `elementsFromPoint` lists them). */
export function dropZoneAt(elements: readonly DropCandidate[]): ZoneId | null {
  for (const el of elements) {
    const target = el.closest?.(`[${DROP_ATTR}]`);
    const zone = target?.getAttribute(DROP_ATTR);
    if (zone) return zone;
  }
  return null;
}

function hitTest(x: number, y: number): ZoneId | null {
  if (typeof document === "undefined" || !document.elementsFromPoint) return null;
  return dropZoneAt(document.elementsFromPoint(x, y));
}

/** The pointer moved while `panel`'s window is dragged. */
export function moveDockDrag(panel: PanelId, x: number, y: number, hit: (x: number, y: number) => ZoneId | null = hitTest): void {
  const s = dockDragStore.getState();
  if (s.panel !== panel) {
    // The targets render on the first move; they can be hit from the next.
    dockDragStore.setState({ panel, over: null });
    return;
  }
  const over = hit(x, y);
  if (over !== s.over) dockDragStore.setState({ over });
}

/** The drag ended: the zone the window was dropped on, if any. */
export function endDockDrag(x: number, y: number, hit: (x: number, y: number) => ZoneId | null = hitTest): ZoneId | null {
  const was = dockDragStore.getState().panel;
  const zone = was ? hit(x, y) : null;
  dockDragStore.setState({ panel: null, over: null });
  return zone;
}

/** A mouse or touch event's pointer position. */
export function pointerOf(e: unknown): { x: number; y: number } | null {
  const m = e as { clientX?: number; clientY?: number; changedTouches?: ArrayLike<{ clientX: number; clientY: number }> };
  if (typeof m.clientX === "number" && typeof m.clientY === "number") return { x: m.clientX, y: m.clientY };
  const t = m.changedTouches?.[0];
  return t ? { x: t.clientX, y: t.clientY } : null;
}
