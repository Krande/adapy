import React from "react";
import * as THREE from "three";

import { sceneRef, cameraRef, rendererRef } from "@/state/refs";
import { requestRender } from "@/state/perfStore";
import { useModelState } from "@/state/modelState";
import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { useObjectInfoStore } from "@/state/objectInfoStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { useTypeIconsStore } from "@/state/typeIconsStore";
import { pipeCentroidWorld } from "@/utils/viewer/pipeTrace";
import {
  boltSprite,
  diskSprite,
  dropSprite,
  warnSprite,
  disposeSprite,
} from "@/utils/viewer/iconSprites";
import {
  classifyEquipment,
  classifyMedium,
  missingInputs,
  type EquipIcon,
  type MediumMarker,
} from "@/utils/viewer/typeIconClassify";

// Headless: draws the Factorio-style type-icon overlay from the cellbuilder
// model (equipment archetype icons, fluid/service markers along system runs, and
// a red "!" over equipment with missing inputs) into a layer-1 group. Renders
// nothing. Mirrors FemConceptsController; store-driven so it needs no GLB
// parsing and works while editing or viewing a procedural model.
const TypeIconController: React.FC = () => {
  React.useEffect(() => {
    let cleanup: (() => void) | null = null;
    let raf = 0;
    const tryInit = () => {
      if (!rendererRef.current || !sceneRef.current || !cameraRef.current) {
        raf = requestAnimationFrame(tryInit);
        return;
      }
      cleanup = init(sceneRef.current);
    };
    tryInit();
    return () => {
      cancelAnimationFrame(raf);
      cleanup?.();
    };
  }, []);
  return null;
};

function equipSprite(kind: EquipIcon): THREE.Sprite {
  switch (kind) {
    case "electrical":
      return boltSprite();
    case "pump":
      return diskSprite("P", "#2f7fd0");
    case "tank":
      return diskSprite("T", "#3a9d8f");
    default:
      return diskSprite("⚙", "#8a94a6");
  }
}

function mediumSprite(kind: MediumMarker): THREE.Sprite {
  switch (kind) {
    case "water":
      return dropSprite("#2f9be0");
    case "oil":
      return dropSprite("#111111");
    case "electrical":
      return boltSprite();
    case "duct":
      return diskSprite("≈", "#8a94a6");
    default:
      return dropSprite("#8fb8cc");
  }
}

function init(scene: THREE.Scene): () => void {
  const container = new THREE.Group();
  container.name = "__type_icons__";
  container.userData.__excludeFromFit = true;
  scene.add(container);

  const clear = () => {
    for (let i = container.children.length - 1; i >= 0; i--) {
      const o = container.children[i] as THREE.Sprite;
      disposeSprite(o);
      container.remove(o);
    }
  };

  // Icon world size relative to the model (falls back to the cells' extent when
  // no GLB is loaded yet — the cellbuilder case).
  const iconScale = (): number => {
    const bb = useModelState.getState().boundingBox;
    if (bb) return bb.getSize(new THREE.Vector3()).length() * 0.03;
    const cells = Object.values(useCellBuilderStore.getState().cells);
    const maxDim = Math.max(1, ...cells.map((c) => Math.max(...c.size)));
    return maxDim * 0.6;
  };

  const place = (
    sprite: THREE.Sprite,
    x: number,
    y: number,
    z: number,
    s: number,
  ) => {
    sprite.scale.set(s, s, 1);
    sprite.position.set(x, y, z);
    container.add(sprite);
  };

  const rebuild = () => {
    if (!sceneRef.current) return;
    clear();
    const t = useModelState.getState().translation;
    container.position.set(t?.x ?? 0, t?.y ?? 0, t?.z ?? 0);

    const cb = useCellBuilderStore.getState();
    const icons = useTypeIconsStore.getState();
    if (!icons.enabled) {
      requestRender();
      return;
    }

    const s = iconScale();
    const off = s * 0.75;
    const bySlug = new Map(cb.equipmentTypes.map((o) => [o.slug, o]));
    const equipmentCells = Object.values(cb.cells).filter(
      (c) => c.kind === "equipment",
    );

    // every (equipment, port) pair a system connects to (site terminals have no
    // equipment/port and are skipped)
    const connected = new Set<string>();
    for (const sys of Object.values(cb.systems))
      for (const c of sys.connections)
        if (c.equipment && c.port) connected.add(`${c.equipment}::${c.port}`);

    const centerOf = (cell: (typeof equipmentCells)[number]) => ({
      x: cell.origin[0] + cell.size[0] / 2,
      y: cell.origin[1] + cell.size[1] / 2,
      top: cell.origin[2] + cell.size[2],
    });

    for (const cell of equipmentCells) {
      const opt = cell.equipmentType
        ? bySlug.get(cell.equipmentType)
        : undefined;
      const ports = opt?.ports;
      const c = centerOf(cell);
      if (icons.showEquipment) {
        const sprite = equipSprite(
          classifyEquipment(cell.equipmentType, ports),
        );
        sprite.userData.__typeIcon = {
          equipment: cell.name,
          kind: "equipment",
        };
        place(sprite, c.x, c.y, c.top + off, s);
      }
      if (icons.showMissing) {
        const missing = missingInputs(cell.name, ports, connected);
        if (missing.length > 0) {
          const warn = warnSprite();
          warn.userData.__typeIcon = {
            equipment: cell.name,
            kind: "missing",
            missing,
          };
          place(warn, c.x + s * 0.55, c.y, c.top + off + s * 0.85, s * 0.8);
        }
      }
    }

    if (icons.showMedia) {
      const byName = new Map(equipmentCells.map((c) => [c.name, c]));
      for (const sys of Object.values(cb.systems)) {
        // Prefer the real routed pipe: hug the compiled GLB geometry so the
        // marker sits over the actual pipe, not the system bounds. The centroid
        // is world-space; convert to container-local (the container only carries
        // modelState.translation, so subtracting it matches the model coords the
        // equipment icons use). Falls back to the connected-equipment centroid
        // before the model is compiled, or when no matching run is in the scene.
        const world = pipeCentroidWorld(sys.name);
        let cx: number;
        let cy: number;
        let cz: number;
        let lift: number;
        if (world) {
          cx = world.x - (t?.x ?? 0);
          cy = world.y - (t?.y ?? 0);
          cz = world.z - (t?.z ?? 0);
          // The centroid sits on the pipe centerline; lift by a small fraction
          // of the icon size so the marker rests just above the pipe surface
          // rather than floating well over it.
          lift = off * 0.4;
        } else {
          const pts = sys.connections
            .map((c) => (c.equipment ? byName.get(c.equipment) : undefined))
            .filter((c): c is (typeof equipmentCells)[number] => !!c)
            .map(centerOf);
          if (pts.length === 0) continue;
          cx = pts.reduce((a, p) => a + p.x, 0) / pts.length;
          cy = pts.reduce((a, p) => a + p.y, 0) / pts.length;
          cz = Math.max(...pts.map((p) => p.top));
          lift = off * 1.6;
        }
        const sprite = mediumSprite(classifyMedium(sys.type, sys.medium));
        sprite.userData.__typeIcon = { system: sys.name, kind: "medium" };
        place(sprite, cx, cy, cz + lift, s * 0.85);
      }
    }
    requestRender();
  };

  rebuild();

  // Click an equipment icon (including the red "!" missing-input warning) to
  // select that equipment and open the Selected Object Info panel, where its
  // unconnected I/O is listed. The icons live on layer 1, so a dedicated
  // raycaster is needed — the scene's own picker only tests layer 0. Runs on
  // 'click' (after the cellbuilder's pointerup selection) so it wins.
  const raycaster = new THREE.Raycaster();
  raycaster.layers.set(1);
  const pointer = new THREE.Vector2();
  const onClick = (ev: MouseEvent) => {
    const el = rendererRef.current?.domElement;
    const cam = cameraRef.current;
    if (!el || !cam) return;
    const rect = el.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, cam);
    const hits = raycaster.intersectObjects(container.children, false);
    const hit = hits.find(
      (h) => (h.object.userData.__typeIcon as { equipment?: string } | undefined)?.equipment,
    );
    if (!hit) return;
    const info = hit.object.userData.__typeIcon as { equipment?: string };
    const cb = useCellBuilderStore.getState();
    const cell = Object.values(cb.cells).find((c) => c.name === info.equipment);
    if (!cell) return;
    cb.setSelection({ kind: "cell", cellId: cell.id });
    const ois = useObjectInfoStore.getState();
    if (!ois.show_info_box) ois.toggle();
    ev.stopPropagation();
  };
  const clickTarget = rendererRef.current?.domElement ?? null;
  clickTarget?.addEventListener("click", onClick);

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
  // The routed pipe geometry only becomes resolvable once the compiled GLB's
  // model tree is built (post-load); rebuild then so media markers snap from
  // the equipment-centroid fallback onto the real pipe.
  const unsubTree = useTreeViewStore.subscribe((s, prev) => {
    if (s.treeData !== prev.treeData) rebuild();
  });

  return () => {
    clickTarget?.removeEventListener("click", onClick);
    unsubIcons();
    unsubCells();
    unsubModel();
    unsubTree();
    clear();
    scene.remove(container);
    requestRender();
  };
}

export default TypeIconController;
