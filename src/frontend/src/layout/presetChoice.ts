// The layout modes a user can switch between, and the switch. Apart from the
// root (`./LayoutRoot`) so the Options panel reaches them without loading it.

import {CLASSIC_PRESET_ID, DOCKED_PRESET_ID} from "./layout";
import {layoutStore} from "./layoutStore";
import type {PresetId} from "./panel";

/** The presets a user can switch between, in switcher order. */
export const LAYOUT_PRESET_CHOICES: readonly {id: PresetId; label: string}[] = [
  {id: CLASSIC_PRESET_ID, label: "Classic"},
  {id: DOCKED_PRESET_ID, label: "Docked"},
];

/** The preset a page load renders: the saved choice when core has it, else classic. */
export function resolvePreset(active: PresetId | undefined): PresetId {
  return LAYOUT_PRESET_CHOICES.some((c) => c.id === active) ? active! : CLASSIC_PRESET_ID;
}

/** Save `preset` as the user's mode and reload into it. */
export function switchLayoutPreset(preset: PresetId): void {
  layoutStore.getState().setActivePreset(preset === CLASSIC_PRESET_ID ? undefined : preset);
  // After the save has been handed to the backend.
  setTimeout(() => window.location.reload(), 0);
}
