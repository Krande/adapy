// Runtime UI-shell switcher. Renders NOTHING in a stock build (one shell
// registered), so wiring it into the core menu bar is invisible until an image
// is built with an alternative UI overlaid.
//
// Switching persists the choice and reloads: a shell owns the whole component
// tree (canvas, websocket, stores), so hot-swapping one for another mid-session
// would mean tearing all of that down. A reload is both simpler and honest about
// what is happening.

import React from "react";

import { activeUiShellId, listUiShells, setActiveUiShell } from "./uiShells";

interface Props {
  /** Core's top-bar button class factory, so the control matches the menu row. */
  navBtnClass?: (active: boolean) => string;
  className?: string;
}

export const UiShellSwitcher: React.FC<Props> = ({ navBtnClass, className }) => {
  const shells = listUiShells();
  const active = activeUiShellId();
  if (shells.length < 2) return null;

  const wrapper = className ?? navBtnClass?.(false) ?? "";

  return (
    <div className={wrapper} title="Switch the viewer user interface">
      <select
        aria-label="User interface"
        data-testid="ui-shell-switcher"
        className="bg-transparent text-white text-xs outline-none cursor-pointer"
        value={active}
        onChange={(e) => {
          const next = e.target.value;
          if (next !== active) setActiveUiShell(next);
        }}
      >
        {shells.map((s) => (
          <option key={s.id} value={s.id} title={s.description} className="text-black">
            {s.label}
          </option>
        ))}
      </select>
    </div>
  );
};

export default UiShellSwitcher;
