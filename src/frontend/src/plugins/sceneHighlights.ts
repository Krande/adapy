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
// No three.js in this module.

import { srgbHexText } from "@/utils/scene/sceneColor";
import { activeViewport, onActiveViewportChange, type ObjectRef } from "@/viewport/api/viewportApi";
import type { ObjectMarks } from "@/viewport/api/objectColors";

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

interface Entry {
  owner: string;
  color: string;
  refs: ObjectRef[];
}

const entries: Entry[] = [];
let following: (() => void) | null = null;

/** Every highlight as the viewport on screen draws it: newest last, so its colour wins. */
function marks(): ObjectMarks {
  const out = new Map<string, Map<string, string>>();
  for (const entry of entries) {
    for (const { model, id } of entry.refs) {
      let ids = out.get(model);
      if (!ids) out.set(model, (ids = new Map()));
      ids.set(id, entry.color);
    }
  }
  return out;
}

function draw(): void {
  activeViewport()?.setObjectMarks?.(marks());
  // A viewport that comes on screen later (the other renderer) draws them too.
  if (entries.length && !following) following = onActiveViewportChange(() => activeViewport()?.setObjectMarks?.(marks()));
  if (!entries.length && following) {
    following();
    following = null;
  }
}

/** Draw `refs` in `options.color` until `remove()` (or until `owner` is removed from the scene). */
export function highlightObjects(
  owner: string,
  refs: Iterable<SceneHighlightRef>,
  options: SceneHighlightOptions,
): SceneHighlight {
  const entry: Entry = { owner, color: srgbHexText(options.color), refs: copy(refs) };
  entries.push(entry);
  draw();
  return {
    set: (next) => {
      if (!entries.includes(entry)) return;
      entry.refs = copy(next);
      draw();
    },
    remove: () => {
      const at = entries.indexOf(entry);
      if (at < 0) return;
      entries.splice(at, 1);
      draw();
    },
  };
}

/** Take away every highlight `owner` draws (`ctx.scene.remove(owner)`). */
export function endSceneHighlights(owner: string): void {
  const before = entries.length;
  for (let i = entries.length - 1; i >= 0; i--) if (entries[i].owner === owner) entries.splice(i, 1);
  if (entries.length !== before) draw();
}

/** Take away every highlight: the scene was cleared (`clearScene`), and the objects they marked with it.
 *  A handle of one taken away this way does nothing more. */
export function endAllSceneHighlights(): void {
  if (!entries.length) return;
  entries.length = 0;
  draw();
}

/** Read once; no ref the caller hands in is kept. */
function copy(refs: Iterable<SceneHighlightRef>): ObjectRef[] {
  return Array.from(refs, ({ model, id }) => ({ model, id }));
}
