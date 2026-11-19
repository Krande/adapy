// Showing a joint means showing it WITHOUT the frame in front of it.
//
// A joint is a handful of members inside a model of thousands, and at any useful zoom the members
// that matter are behind something. So the panels can put everything else out of the way: either
// gone, or faded to a translucent ghost that keeps the context readable while the joint stays the
// only thing solid. Which one, and how faint the ghost is, is the user's call (`clashCheckStore`'s
// `isolate` / `isolateOpacity`) -- a fixed value is wrong at both ends, unusable on a dense deck
// and pointless on a bare frame.
//
// THROUGH THE VIEWPORT ON SCREEN (`ViewportApi.setObjectFade`, viewport/api/objectFade.ts), so
// either viewport fades: "fade rest" is a fade at the chosen opacity, "hide rest" one at opacity 0.
// It was the three.js scene's own batched meshes, reached from here ("hide the rest" a hidden-range
// set, `CustomBatchedMesh.isolateDrawRanges`, and a ghost what that mesh's hidden material looks
// like, `setHiddenAppearance`): the three.js-free viewport has no such meshes, and faded nothing.
// The three.js viewport still fades that way (utils/scene/threeObjectFade.ts).
//
// Per OBJECT of each model, by the source it was loaded from: a model is one batched mesh (or one
// renderer instance) with a draw range per member, and a group's run has members in several models.
// No three.js here.

import { modelStore } from "@/state/model_worker/modelStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { modelKeyOfSource, type ModelRow } from "@/utils/groups/selectGroup";
import { activeViewport, type ViewportApi } from "@/viewport/api/viewportApi";

export type IsolationMode = "off" | "ghost" | "hidden";

/** The viewport the last fade was put on (null: nothing faded), to be taken off that one even when
 *  another has taken its place since. */
let fadedOn: ViewportApi | null = null;
/** Bumped by every call: a name lookup still under way when another call came is not applied. */
let serial = 0;

/** Put every member back. Safe to call when nothing is isolated. */
export function clearJointIsolation(): void {
  serial++;
  const viewport = fadedOn;
  fadedOn = null;
  if (!viewport) return;
  try {
    viewport.setObjectFade?.(null);
  } catch {
    /* its renderer is gone, and what it faded with it */
  }
}

/** Fade, on the viewport on screen, every object of the models `keep` names but the ones it keeps. */
function fadeRest(keep: ReadonlyMap<string, ReadonlySet<string>>, mode: IsolationMode, opacity: number): void {
  const viewport = activeViewport();
  if (fadedOn && fadedOn !== viewport) clearJointIsolation();
  if (!viewport?.setObjectFade) return;
  // The ghost is never quite gone: at 0 the joint's neighbours would vanish, which is "hide rest".
  viewport.setObjectFade({ keep, opacity: mode === "ghost" ? Math.max(0.01, Math.min(1, opacity)) : 0 });
  fadedOn = viewport;
}

/** Isolate by OBJECT ID, across several loaded models at once: each target's `keep` ids stay solid
 *  and every other object of that model fades (or hides) -- a target with an empty `keep` is a model
 *  faded whole. What a convex-contact run isolates by: its members are element ids per model
 *  (`clashContactPlan.contactIsolationTargets`), never names, which repeat.
 *
 *  A user's own hidden objects stay hidden. Returns how many objects were asked to stay solid; none
 *  asked isolates nothing. */
export function isolateElementRanges(
  targets: readonly { readonly sourceName: string; readonly keep: ReadonlySet<string> }[],
  mode: IsolationMode,
  opacity: number,
): number {
  if (mode === "off" || !targets.some((t) => t.keep.size > 0)) {
    clearJointIsolation();
    return 0;
  }
  serial++;
  const keep = new Map<string, Set<string>>();
  let kept = 0;
  for (const target of targets) {
    kept += target.keep.size;
    const ids = keep.get(target.sourceName) ?? new Set<string>();
    for (const id of target.keep) ids.add(id);
    keep.set(target.sourceName, ids);
  }
  fadeRest(keep, mode, opacity);
  return kept;
}

/** Leave `memberNames` visible in `file` and put the rest out of the way.
 *
 *  Returns how many objects were kept. ZERO keeps nothing isolated: hiding a whole model because a
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
  const asked = ++serial;
  // The model's key, as the model store knows it, from the Scene tree's roots: either viewport lists them.
  const model = modelKeyOfSource(useTreeViewStore.getState().treeData as ModelRow | null, file);
  const pairs = model ? await modelStore.getDrawRangesByMemberNames(model, [...memberNames]) : [];
  if (asked !== serial) return 0; // another call came meanwhile: its answer stands
  if (pairs.length === 0) {
    clearJointIsolation();
    return 0;
  }
  const keep = new Set(pairs.map(([, rangeId]) => String(rangeId)));
  fadeRest(new Map([[file, keep]]), mode, opacity);
  return keep.size;
}
