/**
 * The panel's pinned TAB BAR.
 *
 * Owns: the six tab buttons and their counts. overflow-y-hidden is required:
 * overflow-x-auto alone makes the computed overflow-y `auto` too, so at
 * fractional DPI scaling a 1px sub-pixel rounding spawns a stray vertical
 * scrollbar.
 */

import React from "react";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {PANEL_TAB_LABELS, type PanelTab} from "./chrome";

export const PanelTabBar: React.FC<{
  tab: PanelTab;
  setTab: (t: PanelTab) => void;
  /** The tabs offered, in order (`offeredTabs`). */
  tabs: readonly PanelTab[];
  labels?: Partial<Record<PanelTab, string>>;
}> = ({tab, setTab, tabs, labels}) => {
  const s = useCellBuilderStore();
  const cellCount = Object.keys(s.cells).length;
  const systemCount = Object.keys(s.systems).length;

  const tabBtn = (id: PanelTab, label: string, badge?: number) => (
    <button
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={
        "px-2.5 py-1.5 rounded-t-md font-semibold flex items-center gap-1 border-b-2 -mb-px whitespace-nowrap " +
        (tab === id
          ? "border-blue-400 text-white"
          : "border-transparent text-gray-400 hover:text-white hover:bg-white/5")
      }
    >
      {label}
      {badge != null && (
        <span className="text-[10px] text-gray-400">{badge}</span>
      )}
    </button>
  );

  return (
    <div
      className="shrink-0 flex gap-1 px-2 pt-1.5 border-b border-gray-600/50 overflow-x-auto overflow-y-hidden"
      role="tablist"
    >
      {tabs.map((t) =>
        <React.Fragment key={t}>
          {tabBtn(t, labels?.[t] ?? PANEL_TAB_LABELS[t], t === "build" ? cellCount : t === "systems" ? systemCount : undefined)}
        </React.Fragment>,
      )}
    </div>
  );
};
