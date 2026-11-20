// Leaving a joint's connection view: Esc, or a left click on empty space.
//
// Focusing a joint (or opening a group) with "fade rest" / "hide rest" on puts every other member
// out of the way (`clashIsolation`, driven by `clashJointMarkers`' store sync). Getting out of that
// used to mean finding the joint's row again. Now Esc does it -- unless the key is being typed into
// a field -- and so does a click that hits nothing in the 3D view (a drag that orbits is not a
// click: both viewports tell the two apart before they report a click).
//
// Leaving is what the panels do to drop the cursor: the store's `focusJoint(null)` and, for a group,
// `selectGroup(null)`. The store sync then takes the fade off; `clearJointIsolation` is called too,
// so the full opacity comes back even where that sync is not running. The isolate MODE is left as
// the user set it: the next joint focused is shown the same way.

import { useClashCheckStore } from "@/state/clashCheckStore";
import { clearJointIsolation } from "@/utils/scene/clashIsolation";
import { onViewportPick } from "@/viewport/api/viewportApi";

/** Whether the connection view is on: something focused, and the rest faded or hidden. */
export function jointViewActive(): boolean {
  const s = useClashCheckStore.getState();
  return s.isolate !== "off" && (s.selectedJoint !== null || s.selectedGroup !== null);
}

/** Leave the connection view. Returns false when it was not on (nothing is changed then). */
export function exitJointView(): boolean {
  if (!jointViewActive()) return false;
  const store = useClashCheckStore.getState();
  if (store.selectedJoint !== null) store.focusJoint(null);
  if (useClashCheckStore.getState().selectedGroup !== null) store.selectGroup(null);
  clearJointIsolation();
  return true;
}

/** Whether a key event is the view's way out: Esc alone, not typed into a field. */
export function isJointViewExitKey(e: {
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  target: EventTarget | null;
}): boolean {
  if (e.key !== "Escape" || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return false;
  const target = e.target as { tagName?: unknown; isContentEditable?: unknown } | null;
  if (target && (target.isContentEditable === true || /^(INPUT|TEXTAREA|SELECT)$/.test(String(target.tagName ?? "")))) {
    return false;
  }
  return true;
}

/** A left click that hit no object in the 3D view (the three.js viewport's pointer handler calls
 *  this; the three.js-free one announces it as a pick of nothing). */
export function onEmptySpaceClick(): void {
  exitJointView();
}

let installed: (() => void) | null = null;

/** Listen for the ways out, once for the app (later calls are no-ops). Returns the remover. */
export function installJointViewExit(target: Pick<Window, "addEventListener" | "removeEventListener"> = window): () => void {
  if (installed) return installed;
  const onKey = (e: KeyboardEvent) => {
    if (e.defaultPrevented || !isJointViewExitKey(e)) return;
    if (exitJointView()) e.preventDefault();
  };
  target.addEventListener("keydown", onKey as EventListener);
  const offPick = onViewportPick((pick) => {
    if (pick.ref === null) onEmptySpaceClick();
  });
  installed = () => {
    target.removeEventListener("keydown", onKey as EventListener);
    offPick();
    installed = null;
  };
  return installed;
}
