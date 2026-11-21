// `ctx.modes` (1.34.0): what a plugin may know and ask of the mode of the UI on screen.
//
// A plugin never switches mode by itself: a mode change is the user's. What it may do is follow
// through on something the user just did -- they pressed Replay on a robot cell's run, so the robot
// cell's mode is where that run is looked at. `activate` is for that, and only for that.
//
// Core's own (classic) UI has no modes. There every call here is a no-op: `active` is null,
// `offered` is false and `activate` returns false, so a plugin calls it unconditionally.

import { activeShellMode, listShellModes, setShellMode, subscribeShellModes } from "@/layout/shellModes";
import { modeAvailable } from "@/layout/modeRequirements";
import type { ModeId } from "@/layout/modes";

/** Why a plugin asks for a mode: shown nowhere yet, logged, and kept for a later switcher hint. */
export interface PluginModeActivateOptions {
  /** One line: what the user did that brings them here ("Replay of run 12"). */
  reason?: string;
}

/** `ctx.modes`: the mode of the UI on screen. */
export interface PluginModes {
  /** The active mode's id; null in a UI without modes (core's own). */
  readonly active: ModeId | null;
  /** Does the UI on screen offer mode `id` now (offered, and its requirement holds)? */
  offered: (id: ModeId) => boolean;
  /**
   * Switch to mode `id`, after something the user did. False, and nothing changes, when the UI on
   * screen does not offer it (core's own UI offers none); true when it is the active mode after.
   */
  activate: (id: ModeId, opts?: PluginModeActivateOptions) => boolean;
  /** Called with the active mode on every switch; returns the unsubscribe. */
  subscribe: (cb: (active: ModeId | null) => void) => () => void;
}

/** Does the UI on screen offer mode `id` now. */
export function modeOffered(id: ModeId): boolean {
  const mode = listShellModes().find((m) => m.id === id);
  return !!mode && modeAvailable(mode);
}

/** Switch to `id` when it is offered; false otherwise. `log` hears why, for the console. */
export function activateMode(id: ModeId, opts?: PluginModeActivateOptions, log?: (msg: string) => void): boolean {
  if (!modeOffered(id)) return false;
  if (activeShellMode() === id) return true;
  const ok = setShellMode(id);
  if (ok && log) log(`mode -> ${id}${opts?.reason ? ` (${opts.reason})` : ""}`);
  return ok;
}

/** A plugin's `ctx.modes`; `log` is the plugin's own console line. */
export function makePluginModes(log?: (msg: string) => void): PluginModes {
  return {
    get active() {
      return activeShellMode();
    },
    offered: modeOffered,
    activate: (id, opts) => activateMode(id, opts, log),
    subscribe(cb) {
      let last = activeShellMode();
      return subscribeShellModes(() => {
        const now = activeShellMode();
        if (now === last) return;
        last = now;
        cb(now);
      });
    },
  };
}
