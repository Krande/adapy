// Runtime UI-shell switcher. Renders NOTHING in a stock build (one shell
// registered), so mounting it is invisible until an image is built with an
// alternative UI overlaid.
//
// Switching persists the choice and reloads: a shell owns the whole component
// tree (canvas, websocket, stores), so hot-swapping one for another mid-session
// would mean tearing all of that down. A reload is both simpler and honest about
// what is happening.
//
// LIVES IN THE OPTIONS PANEL, not the menu bar. Which UI you are running is a
// preference set once and rarely revisited — the same kind of thing as the
// theme sitting two sections above it — and a permanent control in the top row
// spends the scarcest space in the app on a decision nobody makes twice a day.

import React from "react";

import { activeUiShellId, listUiShells, setActiveUiShell } from "./uiShells";

interface Props {
  /** Legacy: core's top-bar button class factory. Kept so an existing caller
   *  keeps compiling; the panel form passes neither prop. */
  navBtnClass?: (active: boolean) => string;
  className?: string;
  /**
   * Render the bare `<select>` -- no caption, no description paragraphs.
   *
   * The panel form below is a stacked label: caption, control, what the shell
   * is, and what switching does. That is right in the Options panel it was
   * written for, and wrong in a horizontal bar, where the block grows the row
   * to its own height and shoves the menus out of place. A shell that mounts
   * this in its title bar passes `compact`; the prose moves to the tooltip.
   */
  compact?: boolean;
}

export const UiShellSwitcher: React.FC<Props> = ({ navBtnClass, className, compact }) => {
  const shells = listUiShells();
  const active = activeUiShellId();
  if (shells.length < 2) return null;

  const wrapper = className ?? navBtnClass?.(false) ?? "";
  const current = shells.find((s) => s.id === active);

  const control = (
    <select
      aria-label="User interface"
      data-testid="ui-shell-switcher"
      // Explicit background AND text colour, both from the panel theme.
      // The previous `bg-transparent text-white` rendered the OPTION list
      // white-on-white: a native popup paints its own (light) background and
      // the options inherited the select's white text, so the menu was
      // legible only by luck of the platform. A transparent control cannot
      // colour a popup it does not draw.
      className={
        (compact ? "" : "w-full ") +
        "rounded-sm px-2 py-1 text-xs outline-none cursor-pointer " +
        "bg-[var(--ada-panel-bg)] text-[var(--ada-panel-text)] " +
        "border border-[var(--ada-panel-border)]"
      }
      value={active}
      onChange={(e) => {
        const next = e.target.value;
        if (next !== active) setActiveUiShell(next);
      }}
    >
      {shells.map((s) => (
        <option
          key={s.id}
          value={s.id}
          title={s.description}
          // Options are drawn by the platform, which honours only a
          // background/colour pair set on the option itself. Setting both
          // from the theme is what keeps them readable in either.
          className="bg-[var(--ada-panel-bg)] text-[var(--ada-panel-text)]"
        >
          {s.label}
        </option>
      ))}
    </select>
  );

  // In a bar there is no room for prose, and a stacked label sets the row's
  // height. Everything the panel form spells out is still here, as the
  // control's tooltip.
  if (compact) {
    return (
      <div
        className={wrapper}
        title={
          (current?.description ? current.description + "\n\n" : "") +
          "Switching reloads the viewer — a shell owns the whole component tree."
        }
      >
        {control}
      </div>
    );
  }

  return (
    <div className={wrapper}>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-[var(--ada-panel-text)] opacity-70">User interface</span>
        {control}
        {current?.description && (
          <span className="text-[10px] text-[var(--ada-panel-text)] opacity-60">
            {current.description}
          </span>
        )}
        <span className="text-[10px] text-[var(--ada-panel-text)] opacity-60">
          Switching reloads the viewer — a shell owns the whole component tree.
        </span>
      </label>
    </div>
  );
};

export default UiShellSwitcher;
