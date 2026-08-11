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
 * or return it to the shared origin. The shift is
 * `max(topologyWidth, resultWidth) + gap` (see sideBySideOffsetX), so the two
 * copies clear each other at any model scale — and, crucially, even when the
 * freshly-loaded result group is not yet measurable (0/non-finite width): the
 * caller passes the topology's X-width, which the store always knows from the
 * cells, so the offset never collapses to a tiny value that overlaps.
 * Idempotent: the position is assigned absolutely (base translation ± shift),
 * so re-applying after a recompile or a repeated toggle never drifts. */
export function applySideBySideOffset(
  sourceName: string,
  on: boolean,
  topologyWidthX = 0,
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
  // max-min is position-invariant, so measuring the already-placed group gives
  // the result's own width regardless of any prior offset.
  const box = new THREE.Box3().setFromObject(group);
  const resultWidth = box.max.x - box.min.x;
  group.position.x = baseX + sideBySideOffsetX(topologyWidthX, resultWidth);
  requestRender();
}
