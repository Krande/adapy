import React from "react";

import {
  PositionedMenu,
  type KebabMenuItem,
} from "@/components/common/PositionedMenu";
import { useCellBuilderStore } from "@/state/cellBuilderStore";

// The equipment-port context menu, opened by right-clicking a port arrow in the
// 3D preview (see CellBuilderController.onContextMenu → pickPort). It mirrors
// the cell context menu's Move/Rotate idiom, but targets one port: Move starts
// the translate gizmo on the nozzle (snapping to the equipment bbox corners +
// CAD vertices); Rotate spins the outward direction about the port anchor. Both
// round-trip the edit onto the equipment cell so it survives a recompile.
const CellBuilderPortMenu: React.FC = () => {
  const menu = useCellBuilderStore((s) => s.portMenu);
  const cells = useCellBuilderStore((s) => s.cells);
  const close = useCellBuilderStore((s) => s.closePortMenu);
  const startPortGizmo = useCellBuilderStore((s) => s.startPortGizmo);

  if (!menu) return null;
  const cell = cells[menu.cellId];
  if (!cell) return null;

  const items: KebabMenuItem[] = [
    {
      key: "move",
      label: "Move port",
      title: "Drag the nozzle position — snaps to the bbox corners / CAD vertices",
      onClick: () => startPortGizmo(menu.cellId, menu.portName, "translate"),
    },
    {
      key: "rotate",
      label: "Rotate port",
      title: "Spin the outward direction about the port anchor",
      onClick: () => startPortGizmo(menu.cellId, menu.portName, "rotate"),
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
          <span className="ml-1 text-gray-500">· {menu.portName}</span>
        </span>
      }
    />
  );
};

export default CellBuilderPortMenu;
