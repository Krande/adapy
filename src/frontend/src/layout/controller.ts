// The panel controller a layout host installs (`setPanelController`, `./reveal`):
// what `reveal(id)` / `hide(id)` mean under the active preset.
//
// A panel with a binding is opened and closed through it -- its existing flag
// stays the truth, so every other opener keeps working. A panel without one is
// opened in its placement zone of the layout document (`./reducers`). Either
// way the panel's exclusive group is honoured: revealing one closes the others.

import type { StoreApi } from "zustand/vanilla";

import type { ZoneDef } from "./layout";
import type { LayoutStoreState } from "./layoutStore";
import { placementFor, type PanelId, type PresetId, type RegisteredPanel } from "./panel";
import { closePanel, isOpenIn, openPanel } from "./reducers";
import { getPanel, listPanels } from "./registry";
import type { PanelController } from "./reveal";

export interface LayoutControllerOptions {
  preset: PresetId;
  zones: readonly ZoneDef[];
  store: StoreApi<LayoutStoreState>;
  context?: string;
}

export function createLayoutController({ preset, zones, store, context }: LayoutControllerOptions): PanelController {
  const doc = () => store.getState().doc(preset, zones, context);
  const update = (fn: Parameters<LayoutStoreState["update"]>[2]) => store.getState().update(preset, zones, fn, context);

  const isOpen = (p: RegisteredPanel): boolean => (p.binding ? p.binding.isOpen() : isOpenIn(doc(), p.id));

  const close = (p: RegisteredPanel): void => {
    if (p.binding) p.binding.setOpen(false);
    else update((d) => closePanel(d, p.id));
  };

  /** The other open panels of `p`'s exclusive group. */
  const rivals = (p: RegisteredPanel): RegisteredPanel[] => {
    const group = p.constraints?.exclusiveGroup;
    if (!group) return [];
    return listPanels().filter((o) => o.id !== p.id && o.constraints?.exclusiveGroup === group && isOpen(o));
  };

  return {
    isOpen(id: PanelId) {
      const p = getPanel(id);
      return p ? isOpen(p) : false;
    },
    reveal(id: PanelId) {
      const p = getPanel(id);
      if (!p) return false;
      for (const other of rivals(p)) close(other);
      if (p.binding) {
        p.binding.setOpen(true);
        return true;
      }
      const placement = placementFor(p, preset);
      if (!placement || placement.parent) return false;
      const def = zones.find((z) => z.id === placement.zone);
      if (!def) return false;
      update((d) =>
        openPanel(d, p.id, {
          zone: def.id,
          float: def.kind === "float",
          rect: placement.float ? { x: 80, y: 80, w: placement.float.w, h: placement.float.h } : undefined,
          order: (other) => {
            const o = getPanel(other);
            return o ? placementFor(o, preset)?.order : undefined;
          },
        }),
      );
      return true;
    },
    hide(id: PanelId) {
      const p = getPanel(id);
      if (!p) return false;
      if (!p.binding && !isOpenIn(doc(), id)) return true;
      close(p);
      return true;
    },
  };
}
