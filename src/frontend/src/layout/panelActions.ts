// `reveal` / `hide` / `toggle` for core code, with core's catalogue guaranteed
// registered.
//
// `./reveal` is dependency-free and acts on whatever is registered. Core's own
// call sites import from here instead: the wrappers register core's panels on
// first use (idempotent), so an intent never meets an empty catalogue -- not in
// the embed, not in a test, not in a module that runs before the bootstrap.

import { registerCorePanels } from "./corePanels";
import type { PanelId } from "./panel";
import * as intents from "./reveal";

export { CORE_PANEL_IDS } from "./corePanels";

export function reveal(id: PanelId): boolean {
  registerCorePanels();
  return intents.reveal(id);
}

export function hide(id: PanelId): boolean {
  registerCorePanels();
  return intents.hide(id);
}

export function toggle(id: PanelId): boolean {
  registerCorePanels();
  return intents.toggle(id);
}

export function isPanelOpen(id: PanelId): boolean {
  registerCorePanels();
  return intents.isPanelOpen(id);
}
