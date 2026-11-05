// The grab handle of a mobile bottom sheet: drag to resize the sheet, flick it
// down to dismiss. The behaviour is `useBottomSheet`'s; this is its one piece of
// markup, which three panels used to draw for themselves.

import React from "react";

import type {SheetGrab} from "@/utils/useBottomSheet";

const HANDLE = "shrink-0 flex justify-center items-center py-2 cursor-grab active:cursor-grabbing touch-none";

export interface SheetHandleProps {
  grab: SheetGrab;
  /** Classes before the handle's own, e.g. `sm:hidden` for a panel that is a sheet only on phones. */
  className?: string;
}

export function SheetHandle({grab, className}: SheetHandleProps) {
  return (
    <div
      className={className ? `${className} ${HANDLE}` : HANDLE}
      role="separator"
      aria-label="Drag to resize the panel"
      {...grab}
    >
      <span className="block w-10 h-1.5 rounded-full bg-gray-400/70" aria-hidden="true" />
    </div>
  );
}
