import React from "react";
import { createPortal } from "react-dom";

import { useCellBuilderStore } from "@/state/cellBuilderStore";
import type { CellSide, CellSurface } from "@/utils/cellbuilder/snap";

// The "insert equipment onto/into a cell" popover — opened from the
// + Equipment menu (create a new unit) or an equipment's context menu
// (re-seat that unit). Pick a target cell, the seating surface (floor / roof)
// and the side (top / bottom); the equipment is centred on the cell footprint
// and placed by cellBuilderStore.insertEquipmentIntoCell. Rendered via a body
// portal so it clears the canvas/overlay stacking contexts (like
// PositionedMenu), but with form controls PositionedMenu can't host.

const SIDE_HINT: Record<`${CellSurface}:${CellSide}`, string> = {
  "roof:top": "onto the cell (rests on its roof)",
  "roof:bottom": "into the cell (hung from the roof)",
  "floor:top": "into the cell (standing on the floor)",
  "floor:bottom": "under the cell (hung below the floor)",
};

/** Cell whose footprint contains the equipment's X/Y centre — the natural
 * default target when re-seating. Falls back to the first cell. */
function defaultCellId(
  cells: Record<string, { id: string; kind: string; origin: number[]; size: number[] }>,
  equipmentId: string | null,
): string | null {
  const spaceCells = Object.values(cells).filter((c) => c.kind === "cell");
  if (spaceCells.length === 0) return null;
  const eq = equipmentId ? cells[equipmentId] : null;
  if (eq) {
    const cx = eq.origin[0] + eq.size[0] / 2;
    const cy = eq.origin[1] + eq.size[1] / 2;
    const inside = spaceCells.find(
      (c) =>
        cx >= c.origin[0] &&
        cx <= c.origin[0] + c.size[0] &&
        cy >= c.origin[1] &&
        cy <= c.origin[1] + c.size[1],
    );
    if (inside) return inside.id;
  }
  return spaceCells[0].id;
}

const CellBuilderInsertMenu: React.FC = () => {
  const menu = useCellBuilderStore((s) => s.insertMenu);
  const cells = useCellBuilderStore((s) => s.cells);
  const close = useCellBuilderStore((s) => s.closeInsertMenu);
  const insert = useCellBuilderStore((s) => s.insertEquipmentIntoCell);

  const menuRef = React.useRef<HTMLDivElement>(null);
  const spaceCells = Object.values(cells).filter((c) => c.kind === "cell");

  const [cellId, setCellId] = React.useState<string | null>(null);
  const [surface, setSurface] = React.useState<CellSurface>("roof");
  const [side, setSide] = React.useState<CellSide>("top");

  // Re-seed the target cell each time the popover (re)opens, defaulting to the
  // equipment's containing cell when re-seating.
  React.useEffect(() => {
    if (!menu) return;
    setCellId(defaultCellId(cells, menu.equipmentId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [menu]);

  // Clamp into the viewport and dismiss on outside click / Escape.
  const [style, setStyle] = React.useState<React.CSSProperties>({
    visibility: "hidden",
  });
  React.useLayoutEffect(() => {
    if (!menu) return;
    const el = menuRef.current;
    const w = el?.offsetWidth ?? 240;
    const h = el?.offsetHeight ?? 220;
    const left = Math.max(8, Math.min(menu.x, window.innerWidth - w - 8));
    const top = Math.max(8, Math.min(menu.y, window.innerHeight - h - 8));
    setStyle({ top, left });
    const onDown = (e: Event) => {
      const t = e.target as Node | null;
      if (t && menuRef.current?.contains(t)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu, close]);

  if (!menu) return null;

  const targetName = menu.equipmentId
    ? (cells[menu.equipmentId]?.name ?? "equipment")
    : "New equipment";

  const radioRow = <T extends string>(
    label: string,
    value: T,
    options: readonly { v: T; label: string }[],
    onChange: (v: T) => void,
  ) => (
    <div className="flex items-center gap-2">
      <span className="w-12 text-content">{label}</span>
      <div className="flex gap-1">
        {options.map((o) => (
          <button
            key={o.v}
            type="button"
            onClick={() => onChange(o.v)}
            aria-pressed={value === o.v}
            className={
              "px-2 py-0.5 rounded-sm " +
              (value === o.v
                ? "bg-accent text-white"
                : "bg-surface-2 text-content pointer-fine:hover:bg-surface-3")
            }
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );

  return createPortal(
    <div
      ref={menuRef}
      role="dialog"
      className="fixed z-[70] min-w-[240px] rounded-sm border border-edge bg-surface-0 shadow-lg text-content text-xs p-2 flex flex-col gap-2"
      style={style}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="font-medium text-content border-b border-edge pb-1">
        Insert <span className="text-content-muted">{targetName}</span>
      </div>

      {spaceCells.length === 0 ? (
        <p className="italic text-content-muted">Add a cell first.</p>
      ) : (
        <>
          <div className="flex items-center gap-2">
            <span className="w-12 text-content">cell</span>
            <select
              className="flex-1 min-w-0 text-content bg-surface-2 border border-edge rounded-sm px-1 py-0.5"
              value={cellId ?? ""}
              onChange={(e) => setCellId(e.target.value || null)}
            >
              {spaceCells.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          {radioRow(
            "surface",
            surface,
            [
              { v: "floor", label: "Floor" },
              { v: "roof", label: "Roof" },
            ] as const,
            setSurface,
          )}
          {radioRow(
            "side",
            side,
            [
              { v: "top", label: "Top" },
              { v: "bottom", label: "Bottom" },
            ] as const,
            setSide,
          )}

          <p className="text-content-muted italic">→ {SIDE_HINT[`${surface}:${side}`]}</p>

          <div className="flex items-center gap-1 justify-end pt-0.5">
            <button
              type="button"
              className="px-2 py-1 rounded-sm bg-surface-3 text-white pointer-fine:hover:bg-surface-3"
              onClick={close}
            >
              Cancel
            </button>
            <button
              type="button"
              className="px-2 py-1 rounded-sm bg-accent text-white pointer-fine:hover:bg-accent disabled:opacity-50"
              disabled={!cellId}
              onClick={() => {
                if (!cellId) return;
                insert({
                  equipmentId: menu.equipmentId,
                  cellId,
                  surface,
                  side,
                });
              }}
            >
              Insert
            </button>
          </div>
        </>
      )}
    </div>,
    document.body,
  );
};

export default CellBuilderInsertMenu;
