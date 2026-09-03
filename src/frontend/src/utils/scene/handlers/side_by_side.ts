import * as THREE from "three";

import { loadedSourceGroups, useModelState } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";
import { sideBySideOffsetX } from "@/utils/cellbuilder/sideBySide";

// Side-by-side procedural view: draw the compiled result group beside the
// editable topology instead of on top of it. A single scene, one camera — only
// the (non-interactive) result group moves, so picking, gizmos and the
// cellbuilder keep working exactly as before (the topology stays at the model
// origin). This is the offset half of the "edit topology → see the result live"
// loop; the recompile side is store.compilePreview().

/** Offset a loaded result source to sit just past the +X edge of the topology,
 * or return it to the shared origin. The shift puts the result's LEFT edge past
 * the topology's RIGHT edge plus an aisle (see sideBySideOffsetX), so the two
 * clear each other whatever their spans — including the common case where the
 * topology is authored from the origin outward and the compiled result is
 * centred on it, which a width-only formula could not separate.
 * Idempotent: the position is assigned absolutely (base translation ± shift),
 * so re-applying after a recompile or a repeated toggle never drifts. */
export function applySideBySideOffset(
  sourceName: string,
  on: boolean,
  topologyMaxX = 0,
): void {
  const group = loadedSourceGroups.get(sourceName);
  if (!group) return;
  const translation = useModelState.getState().translation;
  const baseX = translation ? translation.x : 0;
  if (!on) {
    group.position.x = baseX;
    requestRender();
    return;
  }
  // Measure the result in ITS OWN frame: the box is world-space at the group's
  // current position, so subtracting that position gives the geometry's span
  // relative to the shared base — which is what the offset math needs, and is
  // stable across repeated toggles (the group may already be shifted).
  const box = new THREE.Box3().setFromObject(group);
  const resultMinX = box.min.x - group.position.x;
  const resultWidth = box.max.x - box.min.x;
  group.position.x =
    baseX + sideBySideOffsetX(topologyMaxX, resultMinX, Math.max(topologyMaxX, resultWidth));
  requestRender();
}
