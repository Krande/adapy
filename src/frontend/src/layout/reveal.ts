// The intent API for opening and closing panels by id.
//
// Domain code that wants a panel shown -- "a clash was focused, show the Scene
// panel" -- calls `reveal("core:scene")` rather than writing the panel's
// visibility flag itself. Today that is the same write (it goes through the
// panel's binding to the very same flag). The point is that the CALLER no longer
// knows where the flag lives or what showing means: once a layout host exists,
// it decides what revealing a panel is (open its tab, uncollapse its dock, bring
// its float to the front) by installing a controller here, and no call site
// changes.
//
// Dependency-free, like the registry.

import type { PanelId } from "./panel";
import { getPanel } from "./registry";

/** What opening and closing a panel means under the active layout. */
export interface PanelController {
  isOpen(id: PanelId): boolean;
  /** Show the panel (and bring it forward). Returns false when it cannot. */
  reveal(id: PanelId): boolean;
  /** Hide the panel. Returns false when it cannot. */
  hide(id: PanelId): boolean;
}

/**
 * The controller used until a layout host installs its own: every panel's
 * binding, i.e. its existing visibility flag. A panel without a binding has no
 * visibility of its own yet, so it reports closed and cannot be revealed.
 */
export const bindingController: PanelController = {
  isOpen(id) {
    return getPanel(id)?.binding?.isOpen() ?? false;
  },
  reveal(id) {
    const binding = getPanel(id)?.binding;
    if (!binding) return false;
    binding.setOpen(true);
    return true;
  },
  hide(id) {
    const binding = getPanel(id)?.binding;
    if (!binding) return false;
    binding.setOpen(false);
    return true;
  },
};

let _controller: PanelController = bindingController;

/** Install the controller of the active layout; `null` restores the binding controller. */
export function setPanelController(controller: PanelController | null): void {
  _controller = controller ?? bindingController;
}

export function isPanelOpen(id: PanelId): boolean {
  return _controller.isOpen(id);
}

let _elsewhere: ((id: PanelId) => boolean) | null = null;

/**
 * Where a panel shown in ANOTHER browser window is revealed: `fn` brings that
 * window forward and returns true, and this window does not open a second copy.
 * Installed by the main window of a multi-window dashboard (`./windows`); `null` removes it.
 */
export function setRevealElsewhere(fn: ((id: PanelId) => boolean) | null): void {
  _elsewhere = fn;
}

/** Show a panel. Unknown ids and unbound panels are a warned no-op returning false. */
export function reveal(id: PanelId): boolean {
  if (_elsewhere?.(id)) return true;
  const ok = _controller.reveal(id);
  if (!ok) console.warn(`[layout] reveal("${id}"): no such panel, or nothing to reveal`);
  return ok;
}

/** Hide a panel. Unknown ids and unbound panels are a warned no-op returning false. */
export function hide(id: PanelId): boolean {
  const ok = _controller.hide(id);
  if (!ok) console.warn(`[layout] hide("${id}"): no such panel, or nothing to hide`);
  return ok;
}

/** Flip a panel, the way its top-bar toggle does. */
export function toggle(id: PanelId): boolean {
  return isPanelOpen(id) ? hide(id) : reveal(id);
}
