import React from "react";

import { useModelState } from "@/state/modelState";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { useObjectInfoStore } from "@/state/objectInfoStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { useTypeIconsStore } from "@/state/typeIconsStore";
import { systemRouteRanges } from "@/utils/viewer/pipeTrace";
import { boltImage, diskImage, dropImage, warnImage } from "@/utils/viewer/iconImages";
import { layoutTypeIcons, typeIconScale, type TypeIcon } from "@/utils/viewer/typeIconLayout";
import type { EquipIcon, MediumMarker } from "@/utils/viewer/typeIconClassify";
import type { BillboardImage } from "@/viewport/api/billboards";
import { followActiveViewport, onModelLoaded, type ViewportApi } from "@/viewport/api/viewportApi";

// Headless: draws the Factorio-style type-icon overlay from the cellbuilder
// model (equipment archetype icons, fluid/service markers along system runs, and
// a red "!" over equipment with missing inputs) as billboards of whichever
// viewport is on screen. Renders nothing. Store-driven so it needs no GLB
// parsing and works while editing or viewing a procedural model.
const TypeIconController: React.FC = () => {
  React.useEffect(() => followActiveViewport(init), []);
  return null;
};

function equipImage(kind: EquipIcon): BillboardImage {
  switch (kind) {
    case "electrical":
      return boltImage();
    case "pump":
      return diskImage("P", "#2f7fd0");
    case "tank":
      return diskImage("T", "#3a9d8f");
    default:
      return diskImage("⚙", "#8a94a6");
  }
}

function mediumImage(kind: MediumMarker): BillboardImage {
  switch (kind) {
    case "water":
      return dropImage("#2f9be0");
    case "oil":
      return dropImage("#111111");
    case "electrical":
      return boltImage();
    case "duct":
      return diskImage("≈", "#8a94a6");
    default:
      return dropImage("#8fb8cc");
  }
}

const imageOf = (icon: TypeIcon): BillboardImage =>
  icon.kind === "equipment" ? equipImage(icon.icon) : icon.kind === "medium" ? mediumImage(icon.icon) : warnImage();

function init(viewport: ViewportApi): () => void {
  const layer = viewport.billboards("__type_icons__");

  const rebuild = () => {
    layer.clear();
    const icons = useTypeIconsStore.getState();
    if (!icons.enabled) return;
    const cb = useCellBuilderStore.getState();
    const t = useModelState.getState().translation;
    const placed = layoutTypeIcons({
      cells: cb.cells,
      systems: cb.systems,
      equipmentTypes: cb.equipmentTypes,
      show: { equipment: icons.showEquipment, media: icons.showMedia, missing: icons.showMissing },
      scale: typeIconScale(viewport.modelBounds(), cb.cells),
      translation: t ? [t.x, t.y, t.z] : null,
      routeCentre: (system) =>
        viewport.objectsCentre(systemRouteRanges(system).map(([model, id]) => ({ model, id }))),
    });
    for (const icon of placed)
      layer.add({ position: icon.position, image: imageOf(icon), height: icon.size, sizing: "world", tag: icon });
  };

  rebuild();

  // Click an equipment icon (including the red "!" missing-input warning) to
  // select that equipment and open the Selected Object Info panel, where its
  // unconnected I/O is listed. Runs on 'click' (after the cellbuilder's
  // pointerup selection) so it wins.
  const offClick = layer.onClick((tags, ev) => {
    const hit = (tags as (TypeIcon | undefined)[]).find((icon) => icon && icon.kind !== "medium");
    if (!hit || hit.kind === "medium") return;
    const cb = useCellBuilderStore.getState();
    const cell = Object.values(cb.cells).find((c) => c.name === hit.equipment);
    if (!cell) return;
    cb.setSelection({ kind: "cell", cellId: cell.id });
    const ois = useObjectInfoStore.getState();
    if (!ois.show_info_box) ois.toggle();
    ev.stopPropagation();
  });

  const unsubIcons = useTypeIconsStore.subscribe(rebuild);
  const unsubCells = useCellBuilderStore.subscribe((s, prev) => {
    if (
      s.cells !== prev.cells ||
      s.systems !== prev.systems ||
      s.equipmentTypes !== prev.equipmentTypes
    )
      rebuild();
  });
  const unsubModel = useModelState.subscribe((s, prev) => {
    if (
      s.translation !== prev.translation ||
      s.boundingBox !== prev.boundingBox
    )
      rebuild();
  });
  // A viewport that keeps no model state announces its model instead.
  const unsubLoaded = onModelLoaded(rebuild);
  // The routed pipe geometry only becomes resolvable once the compiled GLB's
  // model tree is built (post-load); rebuild then so media markers snap from
  // the equipment-centroid fallback onto the real pipe.
  const unsubTree = useTreeViewStore.subscribe((s, prev) => {
    if (s.treeData !== prev.treeData) rebuild();
  });

  return () => {
    offClick();
    unsubIcons();
    unsubCells();
    unsubModel();
    unsubLoaded();
    unsubTree();
    layer.dispose();
  };
}

export default TypeIconController;
