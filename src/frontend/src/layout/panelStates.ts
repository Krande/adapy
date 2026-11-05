// What a host needs to know about each panel to render it -- is it open, is it
// offered -- and when that may have changed.
//
// A host re-renders on the panels' own notifications: a binding's `subscribe`
// (the visibility flag moved) and a spec's `watch` (something `available` reads
// moved). `panelStatesKey` condenses the answers into one string, so a React host
// can hand `subscribePanels` and the key to `useSyncExternalStore` and render
// only when an answer actually changed. Dependency-free.

import type { PanelEnv, PanelId, RegisteredPanel } from "./panel";
import { isPanelAvailable } from "./registry";
import type { PanelController } from "./reveal";

export interface PanelState {
  open: boolean;
  available: boolean;
}

/** Subscribe to everything that can change a panel's state; returns the unsubscribe. */
export function subscribePanels(
  panels: readonly RegisteredPanel[],
  cb: () => void,
  extra: ReadonlyArray<(cb: () => void) => () => void> = [],
): () => void {
  const unsubs: Array<() => void> = [];
  for (const p of panels) {
    if (p.binding) unsubs.push(p.binding.subscribe(cb));
    if (p.watch) unsubs.push(p.watch(cb));
  }
  for (const sub of extra) unsubs.push(sub(cb));
  return () => {
    for (const u of unsubs) u();
  };
}

/** Each panel's state under `env`, opened per `controller`. */
export function panelStates(
  panels: readonly RegisteredPanel[],
  env: PanelEnv,
  controller: Pick<PanelController, "isOpen">,
): Map<PanelId, PanelState> {
  const out = new Map<PanelId, PanelState>();
  for (const p of panels) out.set(p.id, { open: controller.isOpen(p.id), available: isPanelAvailable(p, env) });
  return out;
}

/** The states as one comparable string: equal exactly when every state is. */
export function panelStatesKey(states: ReadonlyMap<PanelId, PanelState>): string {
  let key = "";
  for (const [id, s] of states) key += `${id}=${s.open ? 1 : 0}${s.available ? 1 : 0};`;
  return key;
}
