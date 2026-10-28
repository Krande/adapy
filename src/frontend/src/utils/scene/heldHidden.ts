// Models that join the scene INVISIBLE until their loader says otherwise.
//
// A caller that loads a whole model only to show part of it (hiding the rest by row once its tree is
// built) would otherwise have every frame between the model's arrival and that hiding draw all of
// it. It names the model here before loading; the model loader adds a matching model's group with
// `visible = false` and records it, and the caller reveals it once the rest is hidden.
//
// Named by an asset's (provider, collection, subject), whatever revision loads: the whole-subject
// model (`assetSourceName` with no `#node`), not a one-node model of the same subject.

import type * as THREE from "three";

import { requestRender } from "@/state/perfStore";

const held = new Map<string, THREE.Object3D[]>();

const keyOf = (provider: string, collection: string, subject: string) => `${provider}/${collection}/${subject}`;

/** Hold the whole-subject model of (provider, collection, subject) hidden when it is added. */
export function holdHidden(provider: string, collection: string, subject: string): void {
  const key = keyOf(provider, collection, subject);
  if (!held.has(key)) held.set(key, []);
}

/** The model loader's question for a model about to join the scene: hide it, and remember it, when
 *  it is held. */
export function takeIfHeld(sourceName: string, group: THREE.Object3D): boolean {
  if (!sourceName.startsWith("assets:") || sourceName.includes("#")) return false;
  const at = sourceName.indexOf("@");
  const key = sourceName.slice("assets:".length, at < 0 ? undefined : at);
  const groups = held.get(key);
  if (!groups) return false;
  group.visible = false;
  groups.push(group);
  return true;
}

/** Show what was held for (provider, collection, subject), and stop holding it. */
export function revealHeld(provider: string, collection: string, subject: string): void {
  const key = keyOf(provider, collection, subject);
  const groups = held.get(key);
  held.delete(key);
  if (!groups?.length) return;
  for (const g of groups) g.visible = true;
  requestRender();
}
