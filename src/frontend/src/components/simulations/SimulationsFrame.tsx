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
  "flex flex-col gap-2 text-xs text-white overflow-hidden " +
  // phone: docked to the bottom edge, full width, the body scrolling within the sheet
  "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] px-2 pb-2 bg-gray-900/95 rounded-t-2xl " +
  // desktop: the card in the menu column, as it has always been
  "sm:static sm:z-auto sm:w-auto sm:max-h-none sm:p-2 sm:bg-gray-900/70 sm:rounded-md " +
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
