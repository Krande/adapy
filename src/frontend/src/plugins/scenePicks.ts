// A plugin taking the scene's clicks for a while (`ctx.scene.pickObjects`, plugin API 1.26.0).
//
// Core's click selects what is under the cursor. A tool that asks the user to point at things --
// "click the two members of the connection" -- needs the click itself: which object it hit, without
// the selection changing under the tool and without an undo step per click. This is that: while a
// plugin holds a pick, a click on an object of a loaded model is handed to it first, by whichever
// viewport is on screen, and a click it takes selects nothing.
//
// Dependency-free: the two viewports' click handlers and the plugin context share it, and the
// facade's root entry may re-export its types.

/** What was clicked: an object of a loaded model. */
export interface ScenePick {
  /** The model the object is in (its key in the scene). */
  model: string;
  /** The object in that model: its draw-range id, as selection and the trees name it. */
  id: string;
  /** The object's name as the model's file gives it, when it has one. */
  name: string | null;
  /** The file the model was loaded from, when it is known. */
  sourceName: string | null;
  /** Where on the object, in the model's own coordinates; null where the viewport does not say. */
  point: [number, number, number] | null;
  /** The click carried the key that adds to a selection (shift, ctrl, cmd). */
  additive: boolean;
}

export interface ScenePickOptions {
  /** Handed each click on an object. The click is the plugin's -- nothing is selected for it --
   *  unless this returns false, which lets it through to whoever asked before, then to core. */
  onPick: (pick: ScenePick) => boolean | void;
}

/** A pick being held; `end` gives the clicks back. */
export interface ScenePickSession {
  end: () => void;
}

interface Holder {
  owner: string;
  onPick: ScenePickOptions["onPick"];
}

const holders: Holder[] = [];

/** Take the scene's clicks on objects until `end()` (or until `owner` is removed from the scene). */
export function pickObjects(owner: string, options: ScenePickOptions): ScenePickSession {
  const holder: Holder = { owner, onPick: options.onPick };
  holders.push(holder);
  return {
    end: () => {
      const at = holders.indexOf(holder);
      if (at >= 0) holders.splice(at, 1);
    },
  };
}

/** Whether anybody holds a pick: a viewport asks before it works out what was clicked. */
export function scenePicksHeld(): boolean {
  return holders.length > 0;
}

/** Hand a click to whoever holds a pick, newest first. True when it was taken: the viewport then
 *  selects nothing. A holder that throws loses its pick rather than every click after it. */
export function offerScenePick(pick: ScenePick): boolean {
  for (let i = holders.length - 1; i >= 0; i--) {
    const holder = holders[i];
    try {
      if (holder.onPick(pick) !== false) return true;
    } catch (err) {
      holders.splice(holders.indexOf(holder), 1);
      // eslint-disable-next-line no-console
      console.error(`[plugins] ${holder.owner}: its scene pick threw and was ended`, err);
    }
  }
  return false;
}

/** End every pick `owner` holds (`ctx.scene.remove(owner)`). */
export function endScenePicks(owner: string): void {
  for (let i = holders.length - 1; i >= 0; i--) if (holders[i].owner === owner) holders.splice(i, 1);
}
