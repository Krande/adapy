// Simulation providers and the open scope's runs, for the Simulations panel.
//
// Providers are fetched once at start-up (they gate whether the panel is offered at all) and
// again whenever the panel asks. Runs are the scope's, newest first; while any of them has not
// ended, the store polls those runs -- and only those -- until they have, so a run started here
// or found running on load is followed without the panel having to stay mounted.
//
// BATCHES. A server that keeps batches (`GET .../simulations/batches`) lists the runs a client
// started together as one line each (`batches`), and `runs` are then only the runs of no batch. A
// batch's own runs are read when its line is opened (`loadBatchRuns`), a page at a time, so a batch
// of hundreds costs a request, not hundreds. While a batch has runs going, its line is asked for
// again every few rounds (`BATCH_POLL_EVERY`) and so are the pages of its runs that are open -- one
// request each -- rather than every run of it on its own. A server without batches lists every
// run as before.
//
// Built by a factory around the API client and a timer, so the unit tests drive it with a fake
// of each; `useSimulationStore` is the one the app uses.

import { create, type StoreApi, type UseBoundStore } from "zustand";

import {
  isActiveRun,
  simulationsApi,
  type SimulationBatch,
  type SimulationDomain,
  type SimulationProviderInfo,
  type SimulationRun,
  type StartSimulationBody,
} from "@/services/api/simulations";
import type { ScopeUrl } from "@/services/api/client";
import { NOT_CANCELLED } from "@/services/simulations/batchRows";

/** How often an unfinished run is asked for again. */
export const SIMULATION_POLL_MS = 1500;
/** Every how many polling rounds a batch that has runs going is asked for again. */
export const BATCH_POLL_EVERY = 4;
/** How many of a batch's runs one page holds. */
export const BATCH_PAGE = 50;
/** The most runs of a batch one refresh asks for again (the server's largest page). */
const BATCH_REFRESH_MAX = 200;

/** The calls the store makes; `simulationsApi` satisfies it. Without `listBatches` and
 *  `listRunsPage` the store lists runs as a server without batches does. */
export type SimulationStoreApi = Pick<
  typeof simulationsApi,
  "listProviders" | "listRuns" | "getRun" | "startRun" | "cancelRun"
> &
  Partial<Pick<typeof simulationsApi, "listBatches" | "listRunsPage">>;

/** `setTimeout`, as a function that returns its own cancel. */
export type Schedule = (fn: () => void, ms: number) => () => void;

const browserSchedule: Schedule = (fn, ms) => {
  const id = setTimeout(fn, ms);
  return () => clearTimeout(id);
};

/** The runs of one batch read so far. */
export interface BatchRunsState {
  readonly runs: readonly SimulationRun[];
  /** Where the next page starts; null when every run (of those shown) is read. */
  readonly next: string | null;
  readonly loading: boolean;
  readonly error: string | null;
  /** The cancelled runs are listed too. */
  readonly cancelled: boolean;
}

export interface SimulationState {
  /** The Simulations panel is open (the flag its layout binding reads and writes). */
  panelOpen: boolean;
  domains: readonly SimulationDomain[];
  providers: readonly SimulationProviderInfo[];
  providersLoaded: boolean;
  providersError: string | null;
  /** The scope `runs` belongs to; null before the first load. */
  runsScope: ScopeUrl | null;
  /** The scope's runs; where the server keeps batches, only those of no batch. */
  runs: readonly SimulationRun[];
  runsError: string | null;
  /** The scope's batches, newest first; empty where the server keeps none. */
  batches: readonly SimulationBatch[];
  /** Whether the server keeps batches; null before it was asked. */
  batchesSupported: boolean | null;
  /** The runs read of each batch whose line was opened, by batch id. */
  batchRuns: Readonly<Record<string, BatchRunsState>>;
  /** A start request is in flight. */
  starting: boolean;
  startError: string | null;

  setPanelOpen: (open: boolean) => void;
  loadProviders: () => Promise<void>;
  /** Load `scope`'s runs and follow the unfinished ones. Switching scope drops the old list. */
  loadRuns: (scope: ScopeUrl) => Promise<void>;
  /** Read a batch's runs: the first page, or with `more` the next one. `cancelled` lists its
   *  cancelled runs too (left out it keeps the batch's last choice; hidden at first). */
  loadBatchRuns: (scope: ScopeUrl, batchId: string, opts?: { more?: boolean; cancelled?: boolean }) => Promise<void>;
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

/** `runs` with each of `fresh` in place, the ones it did not have first, newest first. */
export function mergeRuns(runs: readonly SimulationRun[], fresh: readonly SimulationRun[]): SimulationRun[] {
  const byId = new Map(fresh.map((r) => [r.run_id, r]));
  const known = new Set(runs.map((r) => r.run_id));
  return [...fresh.filter((r) => !known.has(r.run_id)), ...runs.map((r) => byId.get(r.run_id) ?? r)];
}

const EMPTY_BATCH: BatchRunsState = { runs: [], next: null, loading: false, error: null, cancelled: false };

export function createSimulationStore(
  api: SimulationStoreApi,
  schedule: Schedule = browserSchedule,
): UseBoundStore<StoreApi<SimulationState>> {
  let cancelTimer: (() => void) | null = null;
  let round = 0;
  /** A run was started in a batch since the batches were listed: list them on the next round. */
  let batchesStale = false;

  return create<SimulationState>((set, get) => {
    const anyActive = () => {
      const s = get();
      return (
        s.runs.some(isActiveRun) ||
        batchesStale ||
        s.batches.some((b) => b.active > 0) ||
        Object.values(s.batchRuns).some((b) => b.runs.some(isActiveRun))
      );
    };

    /** Arm the next round, unless one is armed or nothing is unfinished. */
    const follow = () => {
      if (cancelTimer || !anyActive()) return;
      cancelTimer = schedule(async () => {
        cancelTimer = null;
        if (await get().pollOnce()) follow();
      }, SIMULATION_POLL_MS);
    };

    const patchBatch = (batchId: string, patch: Partial<BatchRunsState>) =>
      set({ batchRuns: { ...get().batchRuns, [batchId]: { ...(get().batchRuns[batchId] ?? EMPTY_BATCH), ...patch } } });

    /** Ask again for the runs of the batches that are open: the ones read so far, in one request each. */
    const refreshOpenBatches = async (scope: ScopeUrl, onlyActive: boolean) => {
      if (!api.listRunsPage) return;
      const open = Object.entries(get().batchRuns).filter(
        ([, b]) => !b.loading && b.runs.length > 0 && (!onlyActive || b.runs.some(isActiveRun)),
      );
      await Promise.all(
        open.map(async ([batchId, b]) => {
          try {
            const page = await api.listRunsPage!(scope, {
              batch: batchId,
              limit: Math.min(BATCH_REFRESH_MAX, Math.max(BATCH_PAGE, b.runs.length)),
              ...(b.cancelled ? {} : { status: NOT_CANCELLED }),
            });
            if (get().runsScope !== scope) return;
            const cur = get().batchRuns[batchId];
            if (!cur || cur.cancelled !== b.cancelled) return; // the choice changed meanwhile
            patchBatch(batchId, { runs: mergeRuns(cur.runs, page.runs), error: null });
          } catch {
            // Tried again on the next round; what is shown stays.
          }
        }),
      );
    };

    const listBatches = async (scope: ScopeUrl): Promise<boolean> => {
      if (!api.listBatches) return false;
      const listed = await api.listBatches(scope);
      if (get().runsScope !== scope) return get().batchesSupported === true;
      if (listed === null) {
        set({ batches: [], batchesSupported: false });
        return false;
      }
      set({ batches: listed.batches, batchesSupported: true });
      return true;
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
      batches: [],
      batchesSupported: null,
      batchRuns: {},
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
        if (get().runsScope !== scope) {
          set({ runsScope: scope, runs: [], runsError: null, batches: [], batchesSupported: null, batchRuns: {} });
        }
        try {
          // The batches first: whether the server keeps them says which runs are listed here.
          let batched = get().batchesSupported === true;
          let batchesError: string | null = null;
          try {
            batched = await listBatches(scope);
            batchesStale = false;
          } catch (err) {
            batchesError = message(err);
          }
          const runs = batched ? await api.listRuns(scope, { batch: "none" }) : await api.listRuns(scope);
          // The scope may have changed while this was in flight; a late answer is dropped.
          if (get().runsScope !== scope) return;
          set({ runs, runsError: batchesError });
          await refreshOpenBatches(scope, false);
          follow();
        } catch (err) {
          if (get().runsScope === scope) set({ runsError: message(err) });
        }
      },

      loadBatchRuns: async (scope, batchId, opts = {}) => {
        if (get().runsScope !== scope) return;
        const cur = get().batchRuns[batchId] ?? EMPTY_BATCH;
        if (!api.listRunsPage) {
          patchBatch(batchId, { error: "this server lists no batches" });
          return;
        }
        const cancelled = opts.cancelled ?? cur.cancelled;
        const more = Boolean(opts.more) && cur.next !== null && cur.cancelled === cancelled;
        patchBatch(batchId, {
          cancelled,
          loading: true,
          error: null,
          // Another choice of what is shown starts over; the next page adds to what is there.
          ...(cur.cancelled !== cancelled ? { runs: [], next: null } : {}),
        });
        try {
          const page = await api.listRunsPage(scope, {
            batch: batchId,
            limit: BATCH_PAGE,
            ...(cancelled ? {} : { status: NOT_CANCELLED }),
            ...(more && cur.next ? { before: cur.next } : {}),
          });
          if (get().runsScope !== scope) return;
          const now = get().batchRuns[batchId] ?? EMPTY_BATCH;
          if (now.cancelled !== cancelled) return; // asked again with the other choice meanwhile
          const runs = more ? [...now.runs, ...page.runs.filter((r) => !now.runs.some((h) => h.run_id === r.run_id))] : page.runs;
          patchBatch(batchId, { runs, next: page.next, loading: false });
          follow();
        } catch (err) {
          if (get().runsScope === scope) patchBatch(batchId, { loading: false, error: message(err) });
        }
      },

      startRun: async (scope, body) => {
        set({ starting: true, startError: null });
        try {
          const run = await api.startRun(scope, body);
          if (get().runsScope !== scope) {
            set({ runsScope: scope, runs: [], batches: [], batchesSupported: null, batchRuns: {} });
          }
          const batchId = run.batch?.id;
          if (batchId && get().batchesSupported) {
            // Its batch's line says it, once the batches are listed again.
            batchesStale = true;
            if (get().batchRuns[batchId]) patchBatch(batchId, { runs: upsertRun(get().batchRuns[batchId].runs, run) });
            set({ starting: false });
          } else {
            set({ runs: upsertRun(get().runs, run), starting: false });
          }
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
          if (get().runsScope === scope) {
            const batchId = run.batch?.id;
            const inBatch = batchId ? get().batchRuns[batchId] : undefined;
            if (batchId && inBatch && inBatch.runs.some((r) => r.run_id === runId)) {
              patchBatch(batchId, { runs: upsertRun(inBatch.runs, run) });
              batchesStale = true;
              set({ runsError: null });
            } else {
              set({ runs: upsertRun(get().runs, run), runsError: null });
            }
          }
          follow();
        } catch (err) {
          set({ runsError: message(err) });
        }
      },

      pollOnce: async () => {
        const scope = get().runsScope;
        if (scope === null) return false;
        round += 1;
        const active = get().runs.filter(isActiveRun);
        const fresh = await Promise.all(
          // A run that cannot be read this round is tried again the next; it keeps its last state.
          active.map((r) => api.getRun(scope, r.run_id).catch(() => null)),
        );
        if (get().runsScope !== scope) return false;
        let runs = get().runs;
        for (const run of fresh) if (run) runs = upsertRun(runs, run);
        set({ runs });

        const batchesGoing = get().batches.some((b) => b.active > 0);
        const batchPagesGoing = Object.values(get().batchRuns).some((b) => b.runs.some(isActiveRun));
        if (get().batchesSupported && (batchesStale || ((batchesGoing || batchPagesGoing) && round % BATCH_POLL_EVERY === 0))) {
          try {
            await listBatches(scope);
            batchesStale = false;
          } catch {
            // The lines stay as they were; asked again later.
          }
          if (get().runsScope !== scope) return false;
          await refreshOpenBatches(scope, true);
          if (get().runsScope !== scope) return false;
        }
        return anyActive();
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
