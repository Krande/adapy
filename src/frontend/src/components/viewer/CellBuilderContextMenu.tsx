import React from "react";

import {
  PositionedMenu,
  type KebabMenuItem,
} from "@/components/common/PositionedMenu";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";

// The cell context menu, opened by a long-press (touch) or right-click
// (desktop) over a builder cell — see CellBuilderController. It offers the two
// direct-manipulation gizmos (Move / Resize), a jump to the parameter panel,
// and delete. Rendered via the shared PositionedMenu portal so it clears the
// canvas/overlay stacking contexts.
const CellBuilderContextMenu: React.FC = () => {
  const menu = useCellBuilderStore((s) => s.contextMenu);
  const cells = useCellBuilderStore((s) => s.cells);
  const close = useCellBuilderStore((s) => s.closeContextMenu);
  const setSelection = useCellBuilderStore((s) => s.setSelection);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const setPanelVisible = useCellBuilderStore((s) => s.setPanelVisible);
  const openInsertMenu = useCellBuilderStore((s) => s.openInsertMenu);
  const openEquipmentEditor = useEquipmentCatalogStore((s) => s.openForSlug);
  const removeCell = useCellBuilderStore((s) => s.removeCell);
  const hasCells = Object.values(cells).some((c) => c.kind === "cell");

  if (!menu) return null;
  const cell = cells[menu.cellId];
  if (!cell) return null;

  // Selecting the cell first so both gizmos and the panel target it; the
  // gizmo is driven off the current selection in the controller.
  const pick = () => setSelection({ kind: "cell", cellId: menu.cellId });

  const items: KebabMenuItem[] = [
    {
      key: "move",
      label: "Move",
      onClick: () => {
        pick();
        setGizmoMode("translate");
      },
    },
    // Resize is a cell-only gizmo — equipment is sized by its type.
    ...(cell.kind === "cell"
      ? [
          {
            key: "resize",
            label: "Resize",
            onClick: () => {
              pick();
              setGizmoMode("resize");
            },
          },
        ]
      : []),
    {
      key: "edit",
      // Equipment size/ports come from its type — edit the type in the catalog
      // panel (changes propagate to every placed instance). Cells edit inline.
      label:
        cell.kind === "equipment" && cell.equipmentType
          ? "Edit type properties…"
          : "Edit properties",
      onClick: () => {
        if (cell.kind === "equipment" && cell.equipmentType) {
          void openEquipmentEditor(cell.equipmentType);
          return;
        }
        pick();
        setPanelVisible(true);
      },
    },
    // Equipment can be re-seated onto/into a cell (same popover as the
    // + Equipment menu, but targeting this existing unit).
    ...(cell.kind === "equipment"
      ? [
          {
            key: "insert",
            label: "Insert onto/into cell…",
            disabled: !hasCells,
            title: hasCells ? undefined : "Add a cell first",
            onClick: () => openInsertMenu(menu.x, menu.y, menu.cellId),
          },
        ]
      : []),
    {
      key: "delete",
      label: "Delete",
      destructive: true,
      separatorBefore: true,
      onClick: () => removeCell(menu.cellId),
    },
  ];

  return (
    <PositionedMenu
      items={items}
      anchor={{ kind: "point", x: menu.x, y: menu.y }}
      onClose={close}
      header={
        <span className="font-medium text-gray-200">
          {cell.name}
          <span className="ml-1 text-gray-500">
            ({cell.kind === "cell" ? "cell" : "equipment"})
          </span>
        </span>
      }
    />
  );
};

export default CellBuilderContextMenu;
