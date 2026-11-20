// The welds of the joint `ada dev connections` previews (Connections > Overlays: "Weld colour",
// "Show welds") on a viewport without a three.js scene. The three.js viewport paints the welds' draw
// ranges of its batched meshes (`connectionWeldTint`); the three.js-free one has no such meshes, so
// here the welds are found by name in the Scene tree and drawn through the viewport's own calls: in
// their colour as a mark (`highlightObjects`, what a plugin's highlight is), hidden with `hide`.
//
// SELECTED WELDS SHOW THE SELECTION. A mark is drawn over the selection's colour (`setObjectMarks`),
// so a weld marked in its colour never looked selected: picking one, or selecting it from the
// Connections tab, left it the weld colour. The weld colour is how the welds are told from their
// members, not a highlight, so the mark follows the selection and leaves a selected weld out: it is
// drawn in the selection's colour, and in the weld colour again once it is let go.
//
// They go with the model they were drawn on: when it leaves the scene (LIFETIME, below), and with
// everything else when the scene is cleared (`clearScene`).
//
// No three.js in this module.

import { useModelState } from "@/state/modelState";
import "@/utils/scene/overlaySources";
import { srgbHexText } from "@/utils/scene/sceneColor";
import { findObjectsByName } from "@/utils/tree_view/modelSource";
import { register, type OverlayHandle } from "@/viewport/api/overlayManager";
import { activeViewport, subscribeSelection, type ObjectRef } from "@/viewport/api/viewportApi";

import type { WeldLook } from "./connectionWeldTint";

const OWNER = "core:connection-preview";

/** The marks, as an entry of the overlay manager (its marks merged with every other entry's). */
let marked: OverlayHandle | null = null;
let markedColor = "";
/** The welds marked in their colour, selected or not. */
let markedRefs: ObjectRef[] = [];
let stopFollowing: (() => void) | null = null;
let hidden: ObjectRef[] = [];
/** The loaded source the welds marked or hidden are of. */
let markedFile: string | null = null;

/** Put back whatever `markWelds` changed. */
export function clearWeldMarks(): void {
  const was = marked;
  marked = null;
  // Removed, the entry puts the rest back (`restore`).
  if (was) was.remove();
  else restore();
}

function restore(): void {
  stopFollowing?.();
  stopFollowing = null;
  markedRefs = [];
  // Shown again only while their model is: a cleared scene has nothing left to show.
  if (hidden.length && markedFile && useModelState.getState().loadedSourceNames.has(markedFile)) activeViewport()?.show(hidden);
  hidden = [];
  markedFile = null;
}

// LIFETIME: the marks are the model's, an overlay with the `model` lifetime: they go when it leaves
// the scene by whichever route, and with every clear of the scene (a replace takes the model too).

/** The marked welds that are not selected now: what is drawn in the weld colour. */
export function unselectedWelds(refs: readonly ObjectRef[], selection: readonly ObjectRef[]): ObjectRef[] {
  if (selection.length === 0) return [...refs];
  const selected = new Set(selection.map((r) => `${r.model}\n${r.id}`));
  return refs.filter((r) => !selected.has(`${r.model}\n${r.id}`));
}

function remark(): void {
  marked?.setMarks(unselectedWelds(markedRefs, activeViewport()?.selection() ?? []), markedColor);
}

/** Draw the welds named `weldNames` of the loaded source `file` as `look` says, through the viewport
 *  on screen. Returns how many objects it found (0: the tree does not list that source yet). */
export function markWelds(file: string | null, weldNames: string[], look: WeldLook): number {
  clearWeldMarks();
  if (!file || (!look.colour && !look.hidden) || weldNames.length === 0) return 0;
  const refs: ObjectRef[] = findObjectsByName(weldNames, { sourceName: file }).map(({ model, id }) => ({ model, id }));
  if (refs.length === 0) return 0;
  markedFile = file;
  const entry = register({
    owner: OWNER,
    id: "welds",
    lifetime: { kind: "model", sources: [file] },
    onReplace: "remove",
    onRemoved: () => {
      if (marked === entry) marked = null;
      restore();
    },
  });
  marked = entry;
  if (look.hidden) {
    activeViewport()?.hide(refs);
    hidden = refs;
  } else if (look.colour) {
    markedRefs = refs;
    markedColor = srgbHexText(look.colour);
    entry.setMarks(unselectedWelds(refs, activeViewport()?.selection() ?? []), markedColor);
    stopFollowing = subscribeSelection(remark);
  }
  return refs.length;
}
