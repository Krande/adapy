// `ctx.simulations` (plugin API 1.21.0): what a plugin can do with simulation runs beside
// registering a provider's UI -- offer a stored file to simulate. Kept apart from `context.ts` so it
// can be tested without the viewer's scene.

import { useSimulationSubjectStore, type SimulationSubject } from "@/state/simulationSubjects";

import type { PluginSimulations } from "./registry";

/** `ctx.simulations` for plugin `pluginId`; `scope` says the open scope, which an offer that names
 *  none is made in. */
export function makePluginSimulations(pluginId: string, scope: () => string): PluginSimulations {
  return {
    offerSubject: (subject) => {
      if (!subject.kind || !subject.key) throw new Error("offerSubject needs a kind and a key");
      const offer: SimulationSubject = {
        kind: subject.kind,
        key: subject.key,
        scope: subject.scope ?? scope(),
        label: subject.label.trim() || subject.key,
        source: subject.source ?? pluginId,
      };
      useSimulationSubjectStore.getState().offer(offer);
      return { withdraw: () => useSimulationSubjectStore.getState().withdraw(offer.key, offer.scope) };
    },
  };
}
