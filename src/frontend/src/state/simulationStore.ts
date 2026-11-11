// Simulation providers and the open scope's runs, for the Simulations panel.
//
// Providers are fetched once at start-up (they gate whether the panel is offered at all) and
// again whenever the panel asks. Runs are the scope's, newest first; while any of them has not
// ended, the store polls those runs -- and only those -- until they have, so a run started here
// or found running on load is followed without the panel having to stay mounted.
//
// Built by a factory around the API client and a timer, so the unit tests drive it with a fake
// of each; `useSimulationStore` is the one the app uses.

import { create, type StoreApi, type UseBoundStore } from "zustand";

import {
  isActiveRun,
  simulationsApi,
  type SimulationDomain,
  type SimulationProviderInfo,
  type SimulationRun,
  type StartSimulationBody,
} from "@/services/api/simulations";
import type { ScopeUrl } from "@/services/api/client";

/** How often an unfinished run is asked for again. */
export const SIMULATION_POLL_MS = 1500;

/** The calls the store makes; `simulationsApi` satisfies it. */
export type SimulationStoreApi = Pick<
  typeof simulationsApi,
  "listProviders" | "listRuns" | "getRun" | "startRun" | "cancelRun"
>;

/** `setTimeout`, as a function that returns its own cancel. */
export type Schedule = (fn: () => void, ms: number) => () => void;

const browserSchedule: Schedule = (fn, ms) => {
  const id = setTimeout(fn, ms);
  return () => clearTimeout(id);
};

export interface SimulationState {
  /** The Simulations panel is open (the flag its layout binding reads and writes). */
  panelOpen: boolean;
  domains: readonly SimulationDomain[];
  providers: readonly SimulationProviderInfo[];
  providersLoaded: boolean;
  providersError: string | null;
  /** The scope `runs` belongs to; null before the first load. */
  runsScope: ScopeUrl | null;
  runs: readonly SimulationRun[];
  runsError: string | null;
  /** A start request is in flight. */
  starting: boolean;
  startError: string | null;

  setPanelOpen: (open: boolean) => void;
  loadProviders: () => Promise<void>;
  /** Load `scope`'s runs and follow the unfinished ones. Switching scope drops the old list. */
  loadRuns: (scope: ScopeUrl) => Promise<void>;
  /** Start a run in `scope`; resolves to it, or null when the server refused (see `startError`). */
  startRun: (scope: ScopeUrl, body: StartSimulationBody) => Promise<SimulationRun | null>;
  cancelRun: (scope: ScopeUrl, runId: string) => Promise<void>;
  /** One polling round: refresh every unfinished run. Resolves to whether any is still going. */
  pollOnce: () => Promise<boolean>;
  /** Stop following runs (the timer); the list stays. */
  stopPolling: () => void;
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** `runs` with `run` in place of the entry with its id, or first when it is new. */
export function upsertRun(runs: readonly SimulationRun[], run: SimulationRun): SimulationRun[] {
  return runs.some((r) => r.run_id === run.run_id)
    ? runs.map((r) => (r.run_id === run.run_id ? run : r))
    : [run, ...runs];
}

export function createSimulationStore(
  api: SimulationStoreApi,
  schedule: Schedule = browserSchedule,
): UseBoundStore<StoreApi<SimulationState>> {
  let cancelTimer: (() => void) | null = null;

  return create<SimulationState>((set, get) => {
    /** Arm the next round, unless one is armed or nothing is unfinished. */
    const follow = () => {
      if (cancelTimer || !get().runs.some(isActiveRun)) return;
      cancelTimer = schedule(async () => {
        cancelTimer = null;
        if (await get().pollOnce()) follow();
      }, SIMULATION_POLL_MS);
    };

    return {
      panelOpen: false,
      domains: [],
      providers: [],
      providersLoaded: false,
      providersError: null,
      runsScope: null,
      runs: [],
      runsError: null,
      starting: false,
      startError: null,

      setPanelOpen: (open) => set({ panelOpen: open }),

      loadProviders: async () => {
        try {
          const res = await api.listProviders();
          set({ domains: res.domains, providers: res.providers, providersLoaded: true, providersError: null });
        } catch (err) {
          // The last good list stays: a failed refresh must not empty the panel.
          set({ providersLoaded: true, providersError: message(err) });
        }
      },

      loadRuns: async (scope) => {
        if (get().runsScope !== scope) set({ runsScope: scope, runs: [], runsError: null });
        try {
          const runs = await api.listRuns(scope);
          // The scope may have changed while this was in flight; a late answer is dropped.
          if (get().runsScope !== scope) return;
          set({ runs, runsError: null });
          follow();
        } catch (err) {
          if (get().runsScope === scope) set({ runsError: message(err) });
        }
      },

      startRun: async (scope, body) => {
        set({ starting: true, startError: null });
        try {
          const run = await api.startRun(scope, body);
          if (get().runsScope !== scope) set({ runsScope: scope, runs: [] });
          set({ runs: upsertRun(get().runs, run), starting: false });
          follow();
          return run;
        } catch (err) {
          set({ starting: false, startError: message(err) });
          return null;
        }
      },

      cancelRun: async (scope, runId) => {
        try {
          const { run } = await api.cancelRun(scope, runId);
          if (get().runsScope === scope) set({ runs: upsertRun(get().runs, run), runsError: null });
          follow();
        } catch (err) {
          set({ runsError: message(err) });
        }
      },

      pollOnce: async () => {
        const scope = get().runsScope;
        if (scope === null) return false;
        const active = get().runs.filter(isActiveRun);
        const fresh = await Promise.all(
          // A run that cannot be read this round is tried again the next; it keeps its last state.
          active.map((r) => api.getRun(scope, r.run_id).catch(() => null)),
        );
        if (get().runsScope !== scope) return false;
        let runs = get().runs;
        for (const run of fresh) if (run) runs = upsertRun(runs, run);
        set({ runs });
        return runs.some(isActiveRun);
      },

      stopPolling: () => {
        cancelTimer?.();
        cancelTimer = null;
      },
    };
  });
}

export const useSimulationStore = createSimulationStore(simulationsApi);

/** Providers in the domains' own order, each domain with its providers; a domain without any is
 *  left out. A provider whose domain the list does not name (a newer server) comes last, under
 *  a group of its own. */
export function providersByDomain(
  domains: readonly SimulationDomain[],
  providers: readonly SimulationProviderInfo[],
): Array<{ domain: SimulationDomain; providers: SimulationProviderInfo[] }> {
  const groups = domains.map((domain) => ({ domain, providers: providers.filter((p) => p.domain === domain.id) }));
  const known = new Set(domains.map((d) => d.id));
  const others = [...new Set(providers.filter((p) => !known.has(p.domain)).map((p) => p.domain))];
  for (const id of others) {
    groups.push({
      domain: { id, title: id, description: "", artefact_roles: [] },
      providers: providers.filter((p) => p.domain === id),
    });
  }
  return groups.filter((g) => g.providers.length > 0);
}
