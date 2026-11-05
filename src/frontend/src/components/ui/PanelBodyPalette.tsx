// Core's bodies drawn outside a panel (a dialog, a flyout, a popover) are not under
// an element marked `PANEL_BODY_ATTR`, so a shell's `bodyPalette` does not reach
// them. Wrapping them here puts them under one.

import React from "react";

import {panelBodyAttrsFor} from "./panelChrome";

export interface PanelBodyPaletteProps extends React.HTMLAttributes<HTMLDivElement> {
  /** The panel whose palette (`PanelOverride.bodyPalette`) applies as well. */
  panelId?: string;
}

/**
 * Marks its children as a core panel body, so the shell's body palette applies to
 * them. Draws no box of its own (`display: contents`) unless given a `className`.
 */
export function PanelBodyPalette({panelId, className, children, ...rest}: PanelBodyPaletteProps) {
  return (
    <div className={className ?? "contents"} {...panelBodyAttrsFor(panelId)} {...rest}>
      {children}
    </div>
  );
}

export default PanelBodyPalette;
