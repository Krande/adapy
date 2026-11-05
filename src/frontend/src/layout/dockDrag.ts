// Dragging a floating panel's header onto a zone to dock it there.
//
// A zone that takes a drop renders an element carrying `DROP_ATTR` (its zone
// id) while a drag is on. Every move hit-tests the pointer with
// `document.elementsFromPoint`, which sees through the dragged window to what is
// under it; the drop docks into the zone found there. Nothing is re-parented:
// docking is a document change, and React draws the panel in its new place.
//
// A target may also carry `DROP_INDEX_ATTR`, the position a drop there inserts
// at (a tab in a strip); with an axis (`DROP_AXIS_ATTR`), past the middle of
// the target along it the position after.
//
// A vanilla zustand store, so the drop targets re-render while the window's own
// drag handlers stay plain functions.

import { createStore } from "zustand/vanilla";

import type { PanelId, ZoneId } from "./panel";

/** On a drop target: the zone a drop there docks into. */
export const DROP_ATTR = "data-layout-drop";
/** On a drop target: the position in its zone a drop there inserts at. */
export const DROP_INDEX_ATTR = "data-layout-drop-index";
/** On an indexed drop target: `x` or `y`, the axis its positions run along. */
export const DROP_AXIS_ATTR = "data-layout-drop-axis";

/** Where a drop lands: a zone, and the position in it (null: at the end). */
export interface DropTarget {
  zone: ZoneId;
  index: number | null;
}

export interface DockDragState {
  /** The panel whose window is being dragged. */
  panel: PanelId | null;
  /** The zone under the pointer, if it takes the drop. */
  over: ZoneId | null;
  /** The position in `over` a drop inserts at; null at the end. */
  index: number | null;
}

export const dockDragStore = createStore<DockDragState>()(() => ({ panel: null, over: null, index: null }));

/** The minimal element surface the hit test reads. */
export interface DropCandidate {
  closest?(selector: string): {
    getAttribute(name: string): string | null;
    getBoundingClientRect?(): { left: number; top: number; width: number; height: number };
  } | null;
}

/** The first drop target among `elements` (topmost first, as `elementsFromPoint` lists them), at pointer `x`, `y`. */
export function dropTargetAt(elements: readonly DropCandidate[], x = 0, y = 0): DropTarget | null {
  for (const el of elements) {
    const target = el.closest?.(`[${DROP_ATTR}]`);
    const zone = target?.getAttribute(DROP_ATTR);
    if (!target || !zone) continue;
    const raw = target.getAttribute(DROP_INDEX_ATTR);
    let index = raw === null ? null : Number.parseInt(raw, 10);
    if (index !== null && Number.isNaN(index)) index = null;
    const axis = index !== null ? target.getAttribute(DROP_AXIS_ATTR) : null;
    const r = axis === "x" || axis === "y" ? target.getBoundingClientRect?.() : undefined;
    if (r && index !== null && (axis === "y" ? y > r.top + r.height / 2 : x > r.left + r.width / 2)) index += 1;
    return { zone, index };
  }
  return null;
}

/** The zone of the first drop target among `elements` (topmost first, as `elementsFromPoint` lists them). */
export function dropZoneAt(elements: readonly DropCandidate[]): ZoneId | null {
  return dropTargetAt(elements)?.zone ?? null;
}

type Hit = (x: number, y: number) => ZoneId | DropTarget | null;

function hitTest(x: number, y: number): DropTarget | null {
  if (typeof document === "undefined" || !document.elementsFromPoint) return null;
  return dropTargetAt(document.elementsFromPoint(x, y), x, y);
}

const asTarget = (t: ZoneId | DropTarget | null): DropTarget | null => (typeof t === "string" ? { zone: t, index: null } : t);

/** The pointer moved while `panel`'s window is dragged. */
export function moveDockDrag(panel: PanelId, x: number, y: number, hit: Hit = hitTest): void {
  const s = dockDragStore.getState();
  if (s.panel !== panel) {
    // The targets render on the first move; they can be hit from the next.
    dockDragStore.setState({ panel, over: null, index: null });
    return;
  }
  const t = asTarget(hit(x, y));
  const over = t?.zone ?? null;
  const index = t?.index ?? null;
  if (over !== s.over || index !== s.index) dockDragStore.setState({ over, index });
}

/** The drag ended: where the window was dropped, if anywhere. */
export function endDockDragAt(x: number, y: number, hit: Hit = hitTest): DropTarget | null {
  const was = dockDragStore.getState().panel;
  const target = was ? asTarget(hit(x, y)) : null;
  dockDragStore.setState({ panel: null, over: null, index: null });
  return target;
}

/** The drag ended: the zone the window was dropped on, if any. */
export function endDockDrag(x: number, y: number, hit: Hit = hitTest): ZoneId | null {
  return endDockDragAt(x, y, hit)?.zone ?? null;
}

/** A mouse or touch event's pointer position. */
export function pointerOf(e: unknown): { x: number; y: number } | null {
  const m = e as { clientX?: number; clientY?: number; changedTouches?: ArrayLike<{ clientX: number; clientY: number }> };
  if (typeof m.clientX === "number" && typeof m.clientY === "number") return { x: m.clientX, y: m.clientY };
  const t = m.changedTouches?.[0];
  return t ? { x: t.clientX, y: t.clientY } : null;
}
