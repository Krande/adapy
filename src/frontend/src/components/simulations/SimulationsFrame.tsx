// The Robotic Simulations panel's classic frame: the menu-column card on desktop, and on phones a
// bottom sheet like the Scene panel's (`useBottomSheet`): drag the handle to resize it, flick it
// down to close it. The heading stays pinned; the body scrolls under it.
//
// Its own module, with nothing but the sheet behind it, so it renders under `node --test` without
// the panel's stores and services.

import React from "react";

import { SheetHandle } from "@/components/ui/SheetHandle";
import { useBottomSheet } from "@/utils/useBottomSheet";

export const SIMULATIONS_FRAME_CLASS =
  // The active panel theme's colours, like the other column cards (`PANEL_SURFACE`): the bodies
  // drawn in it, core's and a plugin's, read the same variables.
  "flex flex-col gap-2 text-xs overflow-hidden bg-[var(--ada-panel-bg)] text-[var(--ada-panel-text)] " +
  // phone: docked to the bottom edge, full width, the body scrolling within the sheet; a theme's
  // background may be translucent, so the scene behind the sheet is blurred
  "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] px-2 pb-2 rounded-t-2xl backdrop-blur-md " +
  // desktop: the card in the menu column
  "sm:static sm:z-auto sm:w-auto sm:max-h-none sm:p-2 sm:rounded-md sm:backdrop-blur-none " +
  "sm:border sm:border-[var(--ada-panel-border)] sm:shadow-lg " +
  "sm:min-w-[300px] sm:max-w-[420px] sm:overflow-visible";

export interface SimulationsFrameProps {
  /** The pinned heading row. */
  header: React.ReactNode;
  /** Flicked down on a phone. */
  onDismiss: () => void;
  children: React.ReactNode;
}

export const SimulationsFrame: React.FC<SimulationsFrameProps> = ({ header, onDismiss, children }) => {
  const { panelRef, sheetStyle, grab } = useBottomSheet(onDismiss);
  return (
    <div ref={panelRef} style={sheetStyle} className={SIMULATIONS_FRAME_CLASS} data-simulations-panel>
      {/* phone grab handle: drag to resize the sheet, flick down to close it */}
      <SheetHandle grab={grab} className="sm:hidden -mb-2" />
      <div className="shrink-0 flex items-center gap-2">{header}</div>
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain sm:overflow-visible">{children}</div>
    </div>
  );
};
