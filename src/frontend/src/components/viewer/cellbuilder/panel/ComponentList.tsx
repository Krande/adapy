/**
 * The procedural model's components -- cells, lofts, openings, equipment -- as a
 * list: select one, retype an opening, delete one.
 *
 * Shown inside the Build tab's "Cells & equipment" section, and on its own as the
 * `core:component-list` panel (`ComponentListBody`), so a shell can dock the list
 * apart from the builder.
 */

import React from "react";

import {PanelCard} from "@/components/ui/PanelFrame";
import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";

/** How many components the open model has. */
export function useComponentCount(): number {
  return useCellBuilderStore((s) => Object.keys(s.cells).length);
}

/** The list itself, scrolling inside the height its host gives it (`maxHeightClass`). */
export const ComponentList: React.FC<{maxHeightClass?: string}> = ({maxHeightClass = "max-h-56"}) => {
  const s = useCellBuilderStore();
  const cells = Object.values(s.cells);
  return (
    <div className={`${maxHeightClass} overflow-y-auto flex flex-col gap-1`} data-component-list="">
      {cells.length === 0 && (
        <p className="italic text-gray-400">
          No cells yet — use + Cell to start, or open a template from the
          storage “＋ New from template” menu.
        </p>
      )}
      {cells.map((c) => (
        <div
          key={c.id}
          data-component-row={c.id}
          className={
            "flex items-center gap-1 border-b border-gray-600/40 pb-0.5 cursor-pointer rounded-sm px-0.5 " +
            (s.selection?.cellId === c.id ? "bg-blue-900/40" : "hover:bg-gray-700/40")
          }
          onClick={() => s.setSelection({kind: "cell", cellId: c.id})}
        >
          <span
            className="inline-block w-2 h-2 rounded-sm shrink-0"
            style={{background: c.kind === "cell" ? "#3b82f6" : c.kind === "loft" ? "#14b8a6" : "#f97316"}}
          />
          <span className="truncate" title={`${c.origin.map((v) => v.toFixed(2))} / ${c.size.map((v) => v.toFixed(2))}`}>
            {c.name}
          </span>
          {c.kind === "equipment" && <span className="text-gray-400">{c.equipmentType ?? "generic"}</span>}
          {c.kind === "opening" && (
            <select
              className="bg-gray-700 text-gray-100 text-[11px] rounded-sm px-1"
              value={c.subtype ?? "door"}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => s.updateCell(c.id, {subtype: e.target.value as "door" | "window"})}
              title="door: jambs + lintel + threshold (cut to floor); window: jambs + head + sill (punched at its height)"
            >
              <option value="door">door</option>
              <option value="window">window</option>
            </select>
          )}
          <button
            className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
            title="Delete"
            onClick={(e) => {
              e.stopPropagation();
              s.removeCell(c.id);
            }}
          >
            🗑
          </button>
        </div>
      ))}
    </div>
  );
};

/**
 * The `core:component-list` panel's content: a count line over the list, filling the
 * height its dock gives it. Nothing to list (a note) while no procedural model is open.
 */
export const ComponentListBody: React.FC = () => {
  const loaded = useCellBuilderStore((s) => s.active !== null || hasEmbeddedDoc(s));
  const count = useComponentCount();
  if (!loaded) return <p className="p-2 text-xs italic text-gray-400">No procedural model is open.</p>;
  return (
    <div className="flex h-full min-h-0 flex-col gap-1 p-2 text-xs text-gray-100" data-component-list-panel="">
      <div className="text-gray-400">
        {count} component{count === 1 ? "" : "s"}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ComponentList maxHeightClass="max-h-none" />
      </div>
    </div>
  );
};

/** The list in the classic UI's card (the menu column), while a model is open and the panel is toggled on. */
export const ComponentListPanel: React.FC = () => (
  <PanelCard variant="surface" className="text-xs pointer-events-auto flex min-w-72 max-w-sm flex-col" heading="Cells & equipment">
    <ComponentListBody />
  </PanelCard>
);

export default ComponentListBody;
