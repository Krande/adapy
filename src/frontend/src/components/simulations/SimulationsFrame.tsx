// The Robotic Simulations panel's classic frame: the menu-column card on desktop, and on phones a
// bottom sheet like the Scene panel's (`useBottomSheet`): drag the handle to resize it, flick it
// down to close it. The heading stays pinned; the body scrolls under it -- on desktop too, where the
// card is never taller than the window: a start form of many fields was cut off below the screen's
// edge with no way to reach its end.
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
  // desktop: the card in the menu column, at most the window's height less the top bar and a margin
  "sm:static sm:z-auto sm:w-auto sm:max-h-[calc(100dvh-5.5rem)] sm:p-2 sm:rounded-md sm:backdrop-blur-none " +
  "sm:border sm:border-[var(--ada-panel-border)] sm:shadow-lg " +
  // It clips on desktop too (it was `sm:overflow-visible`): a frame that does not clip is not
  // shrunk below its content, so beside it the column's other panels were squeezed to a sliver
  // (`layout/presets/columnSlot`). Clipping, it shrinks to its share and its body scrolls.
  "sm:min-w-[300px] sm:max-w-[420px]";

/** The body under the pinned heading: it scrolls, with its scrollbar clear of the content. */
export const SIMULATIONS_BODY_CLASS =
  "flex-1 min-h-0 overflow-y-auto overscroll-contain -mr-2 pr-2 [scrollbar-gutter:stable]";

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
      {/* It scrolled on a phone only: the committed viewer bundle still has the body as
          "flex-1 min-h-0 overflow-y-auto overscroll-contain sm:overflow-visible".
          Its scrollbar goes in the frame's own right padding (`-mr-2 pr-2`), with its room kept
          (`scrollbar-gutter: stable`): the body had none, so a scrollbar drawn over the content
          (an overlay scrollbar, as Windows 11 and phones draw them) covered the right edge of
          every row -- a tab strip's last tab, a select, a button. */}
      <div className={SIMULATIONS_BODY_CLASS} data-simulations-body>
        {children}
      </div>
    </div>
  );
};
