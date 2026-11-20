// Fading objects on the three.js viewport (viewport/api/objectFade.ts): the models it names are
// found by their source (`loadedSourceGroups`, the group each was loaded as), and in each of their
// batched meshes every range but the kept ones goes to the mesh's hidden slot, which draws as a
// translucent ghost (`CustomBatchedMesh.setHiddenAppearance`) -- or not at all, at opacity 0.
//
// That is the hide path, so the ranges a user hid themselves are part of it: they stay out of
// what is kept (a hidden member is not brought back by a fade), and the set each mesh had before is
// put back when the fade goes. Taking it away also gives the hidden slot its plain look back.

import { loadedSourceGroups } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";
import type { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { isBatched } from "@/utils/scene/meshLookup";
import { fadeOpacity, type ObjectFade } from "@/viewport/api/objectFade";

/** The meshes faded now, each with the hidden set it had before. */
let faded: { mesh: CustomBatchedMesh; wasHidden: ReadonlySet<string> }[] = [];

/** Put every faded mesh back as it was. One that is gone (its model cleared) is passed over. */
function restore(): void {
  const meshes = faded;
  faded = [];
  for (const { mesh, wasHidden } of meshes) {
    try {
      mesh.unhideAllDrawRanges();
      if (wasHidden.size > 0) mesh.hideBatchDrawRange(wasHidden);
      mesh.setHiddenAppearance("invisible");
    } catch {
      /* its model was taken off the screen */
    }
  }
}

/** `ViewportApi.setObjectFade` of the three.js viewport. */
export function setThreeObjectFade(fade: ObjectFade | null): void {
  const had = faded.length > 0;
  restore();
  if (fade) {
    const opacity = fadeOpacity(fade.opacity);
    for (const [source, keep] of fade.keep) {
      const group = loadedSourceGroups.get(source);
      if (!group) continue;
      group.traverse((obj) => {
        if (!isBatched(obj)) return;
        // Read after the restore above: the user's own hidden set, not a fade's.
        const wasHidden = new Set(obj.getHiddenRanges());
        const solid = wasHidden.size ? new Set([...keep].filter((id) => !wasHidden.has(id))) : keep;
        obj.setHiddenAppearance(opacity > 0 ? "ghost" : "invisible", opacity);
        obj.isolateDrawRanges(solid);
        faded.push({ mesh: obj, wasHidden });
      });
    }
  }
  if (had || faded.length) requestRender();
}

/** The models were taken off the screen: nothing faded is left to put back. */
export function forgetThreeObjectFade(): void {
  faded = [];
}
