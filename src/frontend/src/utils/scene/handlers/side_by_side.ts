import * as THREE from "three";

import { loadedSourceGroups, useModelState } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";

// Side-by-side procedural view: draw the compiled result group beside the
// editable topology instead of on top of it. A single scene, one camera — only
// the (non-interactive) result group moves, so picking, gizmos and the
// cellbuilder keep working exactly as before (the topology stays at the model
// origin). This is the offset half of the "edit topology → see the result live"
// loop; the recompile side is store.compilePreview().

/** Offset a loaded result source to sit just past the +X edge of the topology,
 * or return it to the shared origin. The gap is the model's own X-width plus a
 * margin, so the two copies clear each other at any model scale. Idempotent:
 * the position is assigned absolutely (base translation ± gap), so re-applying
 * after a recompile or a repeated toggle never drifts. */
export function applySideBySideOffset(sourceName: string, on: boolean): void {
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
  // the model's own width regardless of any prior offset.
  const box = new THREE.Box3().setFromObject(group);
  const width = box.max.x - box.min.x;
  const gap = Number.isFinite(width) && width > 0 ? width * 1.15 : 1;
  group.position.x = baseX + gap;
  requestRender();
}
