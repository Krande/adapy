// The one keydown listener that dispatches the shortcut registry (`./shortcuts`):
// core's keys, a shell's and plugins' own, and non-core panels' toggles.
//
// Reference counted. The layout installs it (`./ShortcutKeys`) and so does a
// mounted viewer (`setupCameraControlsHandlers`), because a viewer can run
// without the layout (the embed); either way there is exactly one listener, so
// no key fires twice.
//
// Keys typed into a field stay the field's, and a key a widget already handled
// (`defaultPrevented`) is left alone.

import type { PanelId } from "./panel";
import { listShortcuts, matchShortcut } from "./shortcuts";

function isTextField(el: EventTarget | null): boolean {
  if (!el || typeof el !== "object") return false;
  const e = el as { tagName?: unknown; isContentEditable?: unknown };
  const tag = typeof e.tagName === "string" ? e.tagName.toUpperCase() : "";
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.isContentEditable === true;
}

let users = 0;
let remove: (() => void) | null = null;
const toggles: Array<(id: PanelId) => void> = [];

function onKey(e: KeyboardEvent): void {
  if (e.defaultPrevented || isTextField(e.target)) return;
  const toggle = toggles[toggles.length - 1];
  const hit = matchShortcut(e, listShortcuts(toggle));
  if (!hit || (e.repeat && !hit.repeat)) return;
  let handled: void | boolean = true;
  try {
    handled = hit.run();
  } catch (err) {
    console.error(`[layout] shortcut "${hit.id}" threw`, err);
    return;
  }
  if (handled !== false && !hit.keepDefault) e.preventDefault();
}

/**
 * Start dispatching shortcuts (a no-op past the first caller); the returned function
 * releases this caller's hold. `toggle` lets non-core panels' shortcuts show or hide them.
 */
export function installShortcutDispatcher(opts: { toggle?: (id: PanelId) => void } = {}): () => void {
  if (typeof window === "undefined") return () => {};
  if (opts.toggle) toggles.push(opts.toggle);
  if (users++ === 0) {
    window.addEventListener("keydown", onKey);
    remove = () => window.removeEventListener("keydown", onKey);
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (opts.toggle) {
      const at = toggles.lastIndexOf(opts.toggle);
      if (at >= 0) toggles.splice(at, 1);
    }
    if (--users === 0) {
      remove?.();
      remove = null;
    }
  };
}
