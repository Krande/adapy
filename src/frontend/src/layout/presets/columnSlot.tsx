// One panel's place in the classic panel column.
//
// PANELS IN THE COLUMN STACK; THEY NEVER OVERLAP. The column (`CLASSIC_COLUMN_CLASS`) is a flex
// column capped at what the top bar leaves of the window, so when its open panels need more than
// that, they are shrunk to fit. Each panel used to be a flex item of the column itself, and how it
// took being shrunk was up to its own root: a card that scrolls (the Selected Object Info card) was
// squeezed to a sliver beside a panel whose root does not (the Connections frame, `overflow:
// visible` on desktop), which kept its height and covered the column; a root that is shrunk while
// its content overflows draws that content over the panel below. So the column gives every panel a
// slot of its own, and the slot -- not the panel -- is the column's flex item:
//
//   * it shrinks with the others, in proportion to what each needs (`min-h-0`): two open panels
//     share the column instead of one taking it all;
//   * it is a scroll box (`overflow-y-auto`): whatever a panel draws stays inside its slot and
//     scrolls there, so it can never be drawn over its neighbour; a panel that scrolls itself
//     (a frame with a pinned header and a scrolling body) shrinks into it and scrolls on its own;
//   * an empty one is not drawn (`empty:hidden`): a closed panel, or one that hides itself, gives
//     its room (and the gap before it) back to the others.
//
// Its own module, free of the classic frame's imports, so `node --test` renders it.

import React from "react";

/** On a column slot: the panel it holds. */
export const COLUMN_SLOT_ATTR = "data-column-slot";

/** A panel's slot in the column: shrinks with the others, scrolls what does not fit, gone when empty. */
export const CLASSIC_COLUMN_SLOT_CLASS = "flex flex-col shrink min-h-0 overflow-y-auto overscroll-contain empty:hidden";

/** Panel `panelId`'s slot in the column, around whatever the panel draws (nothing: no slot shown). */
export function ColumnSlot({panelId, children}: {panelId: string; children?: React.ReactNode}) {
  return (
    <div className={CLASSIC_COLUMN_SLOT_CLASS} {...{[COLUMN_SLOT_ATTR]: panelId}}>
      {children}
    </div>
  );
}
