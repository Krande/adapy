// A plugin marking objects of the loaded models in a colour of its own for a while
// (`ctx.scene.highlightObjects`, plugin API 1.29.0).
//
// A tool that asks the user to point at objects (`ctx.scene.pickObjects`) takes the clicks without
// selecting anything -- so nothing on screen says which objects it has taken. This draws them: each
// highlight is a set of objects and a colour, drawn over the objects' own colour and over the
// selection's, by whichever viewport is on screen (`ViewportApi.setObjectMarks`). It is not the
// selection, not a colour the user gave the object, and no undo step; it goes with `remove()` or
// with `ctx.scene.remove(owner)`. Where two highlights name one object, the newer one's colour shows.
//
// Each highlight is an entry of the overlay manager (viewport/api/overlayManager.ts), which merges
// the marks of every entry into what the viewport on screen draws: a clear of the scene takes it, a
// replace included (its policy), and a viewport that comes on screen draws it.
//
// No three.js in this module.

import { srgbHexText } from "@/utils/scene/sceneColor";
import { register, removeOwner } from "@/viewport/api/overlayManager";

/** sRGB, as CSS: "#ffaa00", or [r, g, b] in 0..1 (`SceneColor`). */
type HighlightColor = string | readonly [number, number, number];

/** An object of a loaded model as a pick names it: `ScenePick.model` and `ScenePick.id`. */
export interface SceneHighlightRef {
  model: string;
  id: string;
}

export interface SceneHighlightOptions {
  /** The colour the objects are drawn in (unlit). */
  color: HighlightColor;
}

/** A highlight being drawn: `set` replaces its objects (an empty list draws none, and keeps the
 *  highlight for later), `remove` takes it away for good. */
export interface SceneHighlight {
  set: (refs: Iterable<SceneHighlightRef>) => void;
  remove: () => void;
}

/** The manager's tag for a highlight, apart from what else an owner draws. */
export const HIGHLIGHT_KIND = "highlight";

/** Draw `refs` in `options.color` until `remove()` (or until `owner` is removed from the scene, or
 *  the scene is cleared). */
export function highlightObjects(
  owner: string,
  refs: Iterable<SceneHighlightRef>,
  options: SceneHighlightOptions,
): SceneHighlight {
  const color = srgbHexText(options.color);
  // What it marks are the models' objects: a replace takes it too.
  const handle = register({ owner, kind: HIGHLIGHT_KIND, lifetime: { kind: "scene" }, onReplace: "remove" });
  handle.setMarks(refs, color);
  return {
    set: (next) => handle.setMarks(next, color),
    remove: () => handle.remove(),
  };
}

/** Take away every highlight `owner` draws (`ctx.scene.remove(owner)`). */
export function endSceneHighlights(owner: string): void {
  removeOwner(owner, HIGHLIGHT_KIND);
}
