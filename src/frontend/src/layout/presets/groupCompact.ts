// A narrow dock cannot fit a group's whole row of header actions beside the
// tabs or the title: below `COMPACT_GROUP_PX` they fold into one "more" menu and
// only that button (and close, which the group draws itself) stays in the bar.
//
// Pure, so it can be tested without a layout.

import type React from "react";

import type { PanelAction, PanelMenuItem } from "../panel";

/** A group narrower than this (px) folds its header actions into one menu. */
export const COMPACT_GROUP_PX = 340;

export const MORE_ACTION_ID = "more";

/**
 * `actions` as the header draws them in a group `width` px wide: unchanged when
 * the group is wide enough (or not measured yet, width 0) or has only one
 * action; else a single "More actions" menu holding all of them. An action with a
 * menu of its own contributes its items, labelled by it.
 */
export function compactActions(actions: readonly PanelAction[], width: number, icon?: React.FC): PanelAction[] {
  if (actions.length <= 1 || width <= 0 || width >= COMPACT_GROUP_PX) return [...actions];
  const items: PanelMenuItem[] = [];
  for (const a of actions) {
    if (a.menu) {
      for (const m of a.menu) items.push({ ...m, id: `${a.id}:${m.id}`, label: `${a.label}: ${m.label}` });
    } else {
      items.push({ id: a.id, label: a.label, run: a.run, disabled: a.disabled, current: a.pressed });
    }
  }
  if (items.length === 0) return [...actions];
  return [{ id: MORE_ACTION_ID, label: "More actions", icon, run: () => {}, menu: items }];
}
