// The provider display names (`@/assets/providerNames`) of each scope, fetched once per scope.
//
// `useProviderName()` is the one way a component turns a provider id into text: it follows the
// scope its viewer is browsing, and re-renders when that scope's names arrive. `providerName(id)`
// is the same answer for code outside React (a label built once, e.g. a loaded model's name),
// reading whatever has arrived for the mounted viewer's scope -- the id until then.

import { useCallback, useEffect } from "react";
import { create } from "zustand";

import { parseProviderLabels, providerDisplayName, type ProviderLabels } from "@/assets/providerNames";
import { assetsApi } from "@/services/api/assets";
import { useViewerStores } from "@/state/AdaViewerContext";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";

interface ProviderNamesState {
  /** scope -> its resolved names. A scope not yet read is absent. */
  byScope: Readonly<Record<string, ProviderLabels>>;
  /** Fetch `scope`'s names, unless they are loaded or loading. `force` re-reads (after an admin
   *  edit). A failed read leaves ids showing -- names are a courtesy, never an error. */
  load: (scope: string, force?: boolean) => Promise<void>;
}

const inflight = new Map<string, Promise<void>>();

export const useProviderNamesStore = create<ProviderNamesState>()((set, get) => ({
  byScope: {},
  load: async (scope, force = false) => {
    if (!force && scope in get().byScope) return;
    const pending = inflight.get(scope);
    if (pending && !force) return pending;
    const run = (async () => {
      let labels: ProviderLabels = {};
      try {
        labels = parseProviderLabels(await assetsApi.getProviderLabels(scope));
      } catch {
        // Ids are shown; the next scope switch or forced reload asks again.
      } finally {
        inflight.delete(scope);
      }
      set((s) => ({ byScope: { ...s.byScope, [scope]: labels } }));
    })();
    inflight.set(scope, run);
    return run;
  },
}));

/** The display name of `id` in the current scope, outside React. The id until names arrive. */
export function providerName(id: string): string {
  const scope = scopeUrlPart(useScopeStore.getState().current);
  return providerDisplayName(id, useProviderNamesStore.getState().byScope[scope]);
}

/** `(id) => display name` for the scope this viewer browses; loads that scope's names on first use. */
export function useProviderName(): (id: string) => string {
  const { useScopeStore: useViewerScope } = useViewerStores();
  const scope = scopeUrlPart(useViewerScope((s) => s.current));
  const labels = useProviderNamesStore((s) => s.byScope[scope]);
  useEffect(() => {
    void useProviderNamesStore.getState().load(scope);
  }, [scope]);
  return useCallback((id: string) => providerDisplayName(id, labels), [labels]);
}
