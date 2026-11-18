// The welds of the joint `ada dev connections` previews (Connections > Overlays: "Weld colour",
// "Show welds") on a viewport without a three.js scene. The three.js viewport paints the welds' draw
// ranges of its batched meshes (`connectionWeldTint`); the three.js-free one has no such meshes, so
// here the welds are found by name in the Scene tree and drawn through the viewport's own calls: in
// their colour as a mark (`highlightObjects`, what a plugin's highlight is), hidden with `hide`.
//
// They go with the model they were drawn on: when it leaves the scene (LIFETIME, below), and with
// everything else when the scene is cleared (`clearScene`).
//
// No three.js in this module.

import { highlightObjects, type SceneHighlight } from "@/plugins/sceneHighlights";
import { useModelState } from "@/state/modelState";
import { findObjectsByName } from "@/utils/tree_view/modelSource";
import { activeViewport, type ObjectRef } from "@/viewport/api/viewportApi";

import type { WeldLook } from "./connectionWeldTint";

const OWNER = "core:connection-preview";

let marked: SceneHighlight | null = null;
let hidden: ObjectRef[] = [];
/** The loaded source the welds marked or hidden are of. */
let markedFile: string | null = null;

/** Put back whatever `markWelds` changed. */
export function clearWeldMarks(): void {
  marked?.remove();
  marked = null;
  // Shown again only while their model is: a cleared scene has nothing left to show.
  if (hidden.length && markedFile && useModelState.getState().loadedSourceNames.has(markedFile)) activeViewport()?.show(hidden);
  hidden = [];
  markedFile = null;
}

// LIFETIME: the marks are the model's, and go when it leaves the scene by whichever route.
useModelState.subscribe((s, prev) => {
  if (s.loadedSourceNames !== prev.loadedSourceNames && markedFile && !s.loadedSourceNames.has(markedFile)) clearWeldMarks();
});

/** Draw the welds named `weldNames` of the loaded source `file` as `look` says, through the viewport
 *  on screen. Returns how many objects it found (0: the tree does not list that source yet). */
export function markWelds(file: string | null, weldNames: string[], look: WeldLook): number {
  clearWeldMarks();
  if (!file || (!look.colour && !look.hidden) || weldNames.length === 0) return 0;
  const refs: ObjectRef[] = findObjectsByName(weldNames, { sourceName: file }).map(({ model, id }) => ({ model, id }));
  if (refs.length === 0) return 0;
  markedFile = file;
  if (look.hidden) {
    activeViewport()?.hide(refs);
    hidden = refs;
  } else if (look.colour) {
    marked = highlightObjects(OWNER, refs, { color: look.colour });
  }
  return refs.length;
}
