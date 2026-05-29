// Shared cache for the /api/components/specs response.
//
// Two consumers:
//   * Menu — uses `hasSpecs` to decide whether the Component-view
//     toggle button is even shown for the current scope.
//   * ComponentControls panel — reads the full specs map to populate
//     the dropdown, the per-role form, and the build pipeline.
//
// Single store so the menu chip and the panel agree on availability,
// and so we fetch once per scope change instead of on every panel
// toggle. Scope reactivity is wired in App-mount via a subscription
// to useScopeStore — see ScopeSpecsSubscriber.
//
// Note: no `branch` param is passed to the backend; the server scans
// every branch under versions/ and returns the newest manifest per
// scope. The bake project's branch name is its own concern.

import {create} from "zustand";

import {viewerApi, type ComponentSpecsResponse} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore, type ScopeOption} from "@/state/scopeStore";

interface ComponentSpecsState {
    /** Last successful specs response, or null if no fetch has
     *  completed yet or the fetch failed. */
    specs: ComponentSpecsResponse | null;
    /** Convenience derived flag — true iff the current-scope sweep
     *  surfaced at least one spec. Drives Menu button visibility. */
    hasSpecs: boolean;
    /** Last fetch error message, or null if the last fetch succeeded
     *  (or none has happened yet). */
    loadError: string | null;
    /** True while a fetch is in flight. Lets the panel show a spinner
     *  on scope switches without flickering the dropdown empty. */
    loading: boolean;
    /** Re-fetch specs for ``scope``. ``scope`` is the URL-form string
     *  the backend expects (``project:<uuid>``, ``user:me``, etc.).
     *  Pass null to clear the cache without fetching. */
    refresh: (scope: string | null) => Promise<void>;
}

export const useComponentSpecsStore = create<ComponentSpecsState>((set) => ({
    specs: null,
    hasSpecs: false,
    loadError: null,
    loading: false,
    refresh: async (scope) => {
        if (scope === null) {
            set({specs: null, hasSpecs: false, loadError: null, loading: false});
            return;
        }
        set({loading: true, loadError: null});
        try {
            const res = await viewerApi.componentsSpecs({scope});
            set({
                specs: res,
                hasSpecs: Object.keys(res.specs).length > 0,
                loadError: null,
                loading: false,
            });
        } catch (err) {
            set({
                specs: null,
                hasSpecs: false,
                loadError: err instanceof Error ? err.message : String(err),
                loading: false,
            });
        }
    },
}));

/** Refresh specs for the currently-selected scope. Use after a manual
 *  reload action or whenever a component build that publishes a new
 *  manifest completes. */
export function refreshComponentSpecsForCurrentScope(): Promise<void> {
    const current = useScopeStore.getState().current;
    return useComponentSpecsStore
        .getState()
        .refresh(current ? scopeUrlPart(current) : null);
}

/** Wire scope changes to a re-fetch. Returns the unsubscribe function.
 *  Mount once from the app root; subsequent scope switches will refetch
 *  specs (and `hasSpecs` flips, hiding/showing the Menu button). */
export function subscribeSpecsToScope(): () => void {
    let lastKey = scopeKey(useScopeStore.getState().current);
    // Initial fetch for whatever scope is already selected on mount.
    void refreshComponentSpecsForCurrentScope();
    return useScopeStore.subscribe((state) => {
        const next = scopeKey(state.current);
        if (next === lastKey) return;
        lastKey = next;
        void refreshComponentSpecsForCurrentScope();
    });
}

function scopeKey(s: ScopeOption | null): string {
    return s ? `${s.kind}:${s.id ?? ""}` : "";
}
