// Toggle buttons for panels, for a shell's own menu bar: the toggles core's top
// bar draws, as one building block. Each button shows and hides its panel
// through `toggle()`, so the panel opens wherever the layout puts it (its home,
// a float, a group of the split grid), and reflects whether it is open.

import React, {useMemo} from "react";

import {navBtnClass} from "@/components/navButton";

import {CORE_PANEL_OWNER, corePanelEnv, registerCorePanels} from "./corePanels";
import {usePanelStates} from "./LayoutHost";
import type {PanelId, RegisteredPanel} from "./panel";
import {getPanel, listToolbarPanels} from "./registry";
import {toggle} from "./reveal";
import {CLASSIC_TOOLBAR} from "./presets/classicRules";
import {PANEL_TOGGLE_ATTR} from "./zoneKeys";

export interface PanelTogglesProps {
  /** The panels, in order. Default: core's top-bar panels (plugin buttons have their own host). */
  ids?: readonly PanelId[];
  /** A button's classes by open state. Default: core's top-bar buttons. */
  buttonClass?: (open: boolean) => string;
}

export function PanelToggles({ids, buttonClass = (open) => navBtnClass(open)}: PanelTogglesProps) {
  registerCorePanels();
  const env = useMemo(() => corePanelEnv(), []);
  const panels: RegisteredPanel[] = ids
    ? ids.map((id) => getPanel(id)).filter((p): p is RegisteredPanel => !!p)
    : listToolbarPanels().filter((p) => p.owner === CORE_PANEL_OWNER);
  const states = usePanelStates(panels, env);
  return (
    <>
      {panels.map((p) => {
        const s = states.get(p.id);
        const recipe = CLASSIC_TOOLBAR[p.id];
        // Not offered here: unavailable, hosted-only on the desktop, or a status menu rather than a toggle.
        if (!s?.available || (p.constraints?.requiresRest && env.restMode !== true) || recipe?.statusMenu) return null;
        const Icon = p.icon;
        const title = recipe?.title(s.open) ?? p.hint ?? p.title;
        return (
          <button
            key={p.id}
            type="button"
            className={buttonClass(s.open)}
            onClick={() => void toggle(p.id)}
            title={title}
            aria-label={recipe?.ariaLabel?.(s.open) ?? title}
            aria-pressed={s.open}
            {...{[PANEL_TOGGLE_ATTR]: p.id}}
          >
            {recipe ? recipe.content : Icon ? <Icon /> : p.title}
          </button>
        );
      })}
    </>
  );
}
