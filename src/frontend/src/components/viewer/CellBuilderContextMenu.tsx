import React from "react";

import {
  PositionedMenu,
  type KebabMenuItem,
} from "@/components/common/PositionedMenu";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import { CORE_PANEL_IDS, reveal } from "@/layout/panelActions";
import { equipmentCadFitted, resizeRefusal } from "@/utils/cellbuilder/cellResize";
import { itemStructure } from "@/utils/cellbuilder/include";
import { BOX_FACE_SIDES } from "@/utils/cellbuilder/snap";

// The cell context menu, opened by a long-press (touch) or right-click
// (desktop) over a builder cell — see CellBuilderController. It offers the two
// direct-manipulation gizmos (Move, Resize for spaces, openings and equipment,
// Rotate for equipment), a jump to the parameter panel, Rename, what can be added
// to it (a space: equipment, and a door / window / hatch on the side clicked; any
// item of a structure: another space in that structure), and delete. Rendered via the
// shared PositionedMenu portal so it clears the canvas/overlay stacking
// contexts.
const CellBuilderContextMenu: React.FC = () => {
  const menu = useCellBuilderStore((s) => s.contextMenu);
  const cells = useCellBuilderStore((s) => s.cells);
  const close = useCellBuilderStore((s) => s.closeContextMenu);
  const setSelection = useCellBuilderStore((s) => s.setSelection);
  const setGizmoMode = useCellBuilderStore((s) => s.setGizmoMode);
  const openInsertMenu = useCellBuilderStore((s) => s.openInsertMenu);
  const openEquipmentEditor = useEquipmentCatalogStore((s) => s.openForSlug);
  const removeCell = useCellBuilderStore((s) => s.removeCell);
  const hasCells = Object.values(cells).some((c) => c.kind === "cell");
  const equipmentCad = useCellBuilderStore((s) => s.equipmentCad);
  const equipmentTypes = useCellBuilderStore((s) => s.equipmentTypes);
  const groups = useCellBuilderStore((s) => s.groups);
  const loftMembers = useCellBuilderStore((s) => s.loftMembers);

  if (!menu) return null;
  const cell = cells[menu.cellId];
  if (!cell) return null;

  // Selecting the cell first so both gizmos and the panel target it; the
  // gizmo is driven off the current selection in the controller.
  const pick = () => setSelection({ kind: "cell", cellId: menu.cellId });

  // Loft bands are read-only swept proxies — offer only "select + jump to
  // panel" (no move/resize/rotate/delete gizmos).
  const isLoft = cell.kind === "loft";
  const structure = itemStructure(cell, cells, groups, loftMembers);
  // The side right-clicked, for a door / window (a wall) or a hatch (floor, roof).
  const side = menu.faceIndex === undefined ? undefined : BOX_FACE_SIDES[menu.faceIndex];
  const st = () => useCellBuilderStore.getState();
  const addOpening = (subtype: "door" | "window" | "opening") => () =>
    st().insertOpeningOnFace(menu.cellId, menu.faceIndex!, subtype);

  const items: KebabMenuItem[] = [
    // Move is a gizmo edit — not for read-only loft bands.
    ...(isLoft
      ? []
      : [
          {
            key: "move",
            label: "Move",
            onClick: () => {
              pick();
              setGizmoMode("translate");
            },
          },
        ]),
    // Resize: spaces, openings and equipment (not a loft band, nor an equipment box
    // fitted to its CAD model; cellResize.ts).
    ...(resizeRefusal(cell.kind, {
      cadFitted: equipmentCadFitted(cell, { equipmentCad, equipmentTypes }),
    }) === null
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
    // Rotate is an equipment-only gizmo — spaces stay axis-aligned. Opens the
    // rotation gizmo plus the manual per-axis degree panel.
    ...(cell.kind === "equipment"
      ? [
          {
            key: "rotate",
            label: "Rotate",
            onClick: () => {
              pick();
              setGizmoMode("rotate");
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
        reveal(CORE_PANEL_IDS.cellbuilder);
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
    // A loft band's name is its member's: renamed with the member, not here.
    ...(isLoft
      ? []
      : [
          {
            key: "rename",
            label: "Rename…",
            onClick: () => st().openRenamePrompt(menu.x, menu.y, menu.cellId),
          },
        ]),
    ...(cell.kind === "cell"
      ? [
          {
            key: "add-equipment",
            label: "Add equipment…",
            separatorBefore: true,
            title: "Seat a new equipment in this space",
            onClick: () => openInsertMenu(menu.x, menu.y, null, menu.cellId),
          },
          ...(side && side.axis !== 2
            ? [
                { key: "add-door", label: `Add door (${side.label} side)`, onClick: addOpening("door") },
                { key: "add-window", label: `Add window (${side.label} side)`, onClick: addOpening("window") },
              ]
            : side
              ? [{ key: "add-hatch", label: `Add hatch (${side.positive ? "roof" : "floor"})`, onClick: addOpening("opening") }]
              : []),
        ]
      : []),
    // Another space in this item's structure: it becomes the active one, and
    // a click in the scene places the space (as + Cell).
    ...(structure
      ? [
          {
            key: "add-space",
            label: `Add space to ${structure}`,
            separatorBefore: cell.kind !== "cell",
            title: "Click in the scene to place it (Esc cancels)",
            onClick: () => {
              st().setActiveGroup(structure);
              st().setMode("add-cell");
            },
          },
        ]
      : []),
    // No Delete for read-only loft bands (they'd just reappear from the raw
    // loft_members on reload).
    ...(isLoft
      ? []
      : [
          {
            key: "delete",
            label: "Delete",
            destructive: true,
            separatorBefore: true,
            onClick: () => removeCell(menu.cellId),
          },
        ]),
  ];

  return (
    <PositionedMenu
      items={items}
      anchor={{ kind: "point", x: menu.x, y: menu.y }}
      onClose={close}
      header={
        <span className="font-medium text-gray-200">
          {cell.name}
          <span className="ml-1 text-gray-500">({cell.kind === "cell" ? "space" : cell.kind})</span>
          {structure && <span className="ml-1 text-gray-500">in {structure}</span>}
        </span>
      }
    />
  );
};

export default CellBuilderContextMenu;
