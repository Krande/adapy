// Showing a joint means showing it WITHOUT the frame in front of it.
//
// A joint is a handful of members inside a model of thousands, and at any useful zoom the members
// that matter are behind something. So the panels can put everything else out of the way: either
// gone, or faded to a translucent ghost that keeps the context readable while the joint stays the
// only thing solid. Which one, and how faint the ghost is, is the user's call (`clashCheckStore`'s
// `isolate` / `isolateOpacity`) -- a fixed value is wrong at both ends, unusable on a dense deck
// and pointless on a bare frame.
//
// Per-DRAW-RANGE, not per-mesh: a model is one batched mesh with a range per member, so "hide the
// rest" is a hidden-range set (`CustomBatchedMesh.isolateDrawRanges`) and a ghost is what that
// mesh's hidden material looks like (`setHiddenAppearance`). Reusing the hide path rather than
// adding a parallel one means the two cannot disagree about which members are which.

import * as THREE from "three";

import { modelStore } from "@/state/model_worker/modelStore";
import { loadedSourceGroups } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";

export type IsolationMode = "off" | "ghost" | "hidden";

/** Meshes currently isolated, each with the hidden set it had BEFORE -- a user's own Shift+H
 *  hides are theirs, and restoring "everything visible" would silently undo them. */
let isolated: { mesh: CustomBatchedMesh; wasHidden: ReadonlySet<string> }[] = [];

function meshesOf(file: string): { meshes: CustomBatchedMesh[]; modelKey: string | null } {
  const group = loadedSourceGroups.get(file);
  const meshes: CustomBatchedMesh[] = [];
  let modelKey: string | null = null;
  if (group) {
    group.traverse((obj: THREE.Object3D) => {
      if (obj instanceof CustomBatchedMesh) {
        meshes.push(obj);
        modelKey ??= (obj as any).unique_key ?? obj.userData?.["unique_hash"] ?? null;
      }
    });
  }
  if (!modelKey) {
    getViewerRuntime().modelKeyMap.current?.forEach((g, key) => {
      if (modelKey === null && g === group) modelKey = key;
    });
  }
  return { meshes, modelKey };
}

/** Put every member back. Safe to call when nothing is isolated. */
export function clearJointIsolation(): void {
  if (isolated.length === 0) return;
  for (const { mesh, wasHidden } of isolated) {
    mesh.unhideAllDrawRanges();
    if (wasHidden.size > 0) mesh.hideBatchDrawRange(wasHidden);
    mesh.setHiddenAppearance("invisible");
  }
  isolated = [];
  requestRender();
}

/** Leave `memberNames` visible in `file` and put the rest out of the way.
 *
 *  Returns how many ranges were kept. ZERO keeps nothing isolated: hiding a whole model because a
 *  name did not resolve would look exactly like a broken viewer, and the honest answer to "these
 *  members are not in this model" is to leave it alone. */
export async function isolateJointMembers(
  file: string | null,
  memberNames: readonly string[],
  mode: IsolationMode,
  opacity: number,
): Promise<number> {
  if (!file || mode === "off" || memberNames.length === 0) {
    clearJointIsolation();
    return 0;
  }
  const { meshes, modelKey } = meshesOf(file);
  if (!modelKey || meshes.length === 0) {
    clearJointIsolation();
    return 0;
  }
  const pairs = await modelStore.getDrawRangesByMemberNames(modelKey, [...memberNames]);
  if (pairs.length === 0) {
    clearJointIsolation();
    return 0;
  }
  const keep = new Set(pairs.map(([, rangeId]) => String(rangeId)));

  clearJointIsolation();
  for (const mesh of meshes) {
    // Captured BEFORE isolating and after the restore above, so what is remembered is the user's
    // own hidden set rather than a previous isolation's.
    const wasHidden = new Set(mesh.getHiddenRanges());
    mesh.setHiddenAppearance(mode === "ghost" ? "ghost" : "invisible", opacity);
    mesh.isolateDrawRanges(keep);
    isolated.push({ mesh, wasHidden });
  }
  requestRender();
  return keep.size;
}
