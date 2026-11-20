// Which loaded models a mode draws (`ModeBehaviour.scene`), applied reversibly. The rules, without
// the scene: `./ModeSceneScope` feeds them the live groups.
//
// A UI declares, per mode, a predicate over the loaded models; core hides what the predicate turns
// down while the mode is active, and puts it back after. The rules:
//
// - What a model's visibility was before a scoped mode touched it is what it goes back to: whether
//   the user had hidden it or not.
// - A model shown or hidden by hand while scoped is the user's: the scope lets go of it and does not
//   set it again -- until the mode changes, when the new mode's rule applies to it again from the
//   visibility the user left it in.
// - A model loaded while scoped is scoped on the pass that sees it, remembered as it arrived; a model
//   unloaded is forgotten.
// - In a mode that declares no scope, every model the scope still holds goes back and the memory
//   empties.
//
// A UI that declares no scope in any mode (core's own UI, which has no modes) is never touched.

import type { ModeId, SceneScopeSpec } from "./modes";

/** A loaded model's group, as far as the scope needs it. */
export interface ScopedGroup {
  visible: boolean;
}

/** What the scope remembers between passes. */
export interface SceneScopeMemory {
  /** What a model looked like when the scope first touched it: what it goes back to. */
  before: Map<string, boolean>;
  /** What the scope last set it to: a model that is not that any more was changed by the user. */
  applied: Map<string, boolean>;
  /** Models the user changed by hand while scoped; the scope has let go of them. */
  released: Set<string>;
  /** The mode of the last pass. */
  mode: ModeId | null;
}

export function newSceneScopeMemory(): SceneScopeMemory {
  return { before: new Map(), applied: new Map(), released: new Set(), mode: null };
}

/**
 * One pass over the loaded models for `mode` and its `scope` (null: the mode declares none).
 * Mutates the groups' visibility and `memory`; returns whether any visibility changed.
 */
export function applyModeSceneScope(
  mode: ModeId | null,
  scope: SceneScopeSpec | null | undefined,
  groups: ReadonlyMap<string, ScopedGroup>,
  memory: SceneScopeMemory,
): boolean {
  if (memory.mode !== mode) {
    // Another mode: what the user took over was an answer to the last mode's view.
    memory.released.clear();
    memory.mode = mode;
  }
  for (const name of [...memory.before.keys()]) {
    const group = groups.get(name);
    if (!group) {
      memory.before.delete(name);
      memory.applied.delete(name);
      memory.released.delete(name);
    } else if (group.visible !== memory.applied.get(name)) {
      memory.before.delete(name);
      memory.applied.delete(name);
      memory.released.add(name);
    }
  }
  for (const name of [...memory.released]) {
    if (!groups.has(name)) memory.released.delete(name);
  }

  let changed = false;
  if (!scope) {
    for (const [name, visible] of memory.before) {
      const group = groups.get(name);
      if (group && group.visible !== visible) {
        group.visible = visible;
        changed = true;
      }
    }
    memory.before.clear();
    memory.applied.clear();
    memory.released.clear();
    return changed;
  }
  for (const [name, group] of groups) {
    if (memory.released.has(name)) continue;
    const was = memory.before.get(name) ?? group.visible;
    // A model the mode draws keeps its own visibility (one the user hid stays hidden); the rest hide.
    const visible = scope.draws({ name }) ? was : false;
    if (!memory.before.has(name)) memory.before.set(name, was);
    memory.applied.set(name, visible);
    if (group.visible !== visible) {
      group.visible = visible;
      changed = true;
    }
  }
  return changed;
}
