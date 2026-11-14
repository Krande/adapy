// The run whose result is open in the viewer, and its console: the run's activity feed (what each
// of its actors is doing and planning, services/simulations/activityFeed.ts), read as it grows.
//
// The Simulations panel names the run when its result is opened (`open`). From then on this store
// follows the run's `activity` artefact -- whoever the provider is, with no plugin involved -- by
// asking for the bytes beyond those it has, the way a recording that is still being written is
// followed, until the run has ended and the feed has been read to its end. The console (the
// component) shows the feed at the playback clock's time; whether it is shown is the user's, and
// remembered.
//
// No three.js in this module.
import {create} from "zustand";

import {isActiveRun, simulationsApi, type SimulationArtefact, type SimulationRun} from "@/services/api/simulations";
import type {ScopeUrl} from "@/services/api/client";
import {ACTIVITY_ROLE, ActivityFeed} from "@/services/simulations/activityFeed";

export const RUN_CONSOLE_POLL_MS = 1500;
const SHOWN_KEY = "ada-run-console-shown";

export type RunConsoleApi = Pick<typeof simulationsApi, "getRun" | "fetchArtefact">;
/** Runs `fn` after `ms`; returns the function that calls it off. */
export type Schedule = (fn: () => void, ms: number) => () => void;

export interface RunConsoleState {
    /** The run whose result is open; null when none is. */
    run: {scope: ScopeUrl; runId: string; title: string} | null;
    /** Its feed, once the run has one and its header has been read. */
    feed: ActivityFeed | null;
    /** Goes up whenever `feed` has more in it: the feed object itself stays the same one. */
    version: number;
    /** Whether the run is still going (the feed may grow). */
    live: boolean;
    /** Why the feed cannot be read, in words for the user. */
    error: string | null;
    /** Whether the user wants the console on screen. */
    shown: boolean;
    setShown: (shown: boolean) => void;
    /** A run's result was opened: follow its feed. Opening another, or the same again, starts over. */
    open: (scope: ScopeUrl, run: SimulationRun, title?: string) => void;
    /** Nothing is open any more. */
    close: () => void;
    /** One look at the run and its feed. Resolves to whether there may be more to come. */
    pollOnce: () => Promise<boolean>;
}

/** The run's activity feed among its artefacts. */
export function activityArtefact(run: Pick<SimulationRun, "artefacts">): SimulationArtefact | undefined {
    return run.artefacts.find((a) => a.role === ACTIVITY_ROLE);
}

function readShown(): boolean {
    try {
        return globalThis.localStorage?.getItem(SHOWN_KEY) === "1";
    } catch {
        return false;
    }
}

function writeShown(shown: boolean): void {
    try {
        globalThis.localStorage?.setItem(SHOWN_KEY, shown ? "1" : "0");
    } catch {
        // no storage (private mode, tests): the choice lasts as long as the page
    }
}

const defaultSchedule: Schedule = (fn, ms) => {
    const handle = setTimeout(fn, ms);
    return () => clearTimeout(handle);
};

export function createRunConsoleStore(api: RunConsoleApi, schedule: Schedule = defaultSchedule) {
    /** Which opening this is: a look that was started for an earlier one is dropped when it answers. */
    let generation = 0;
    let offset = 0;
    let revision = 0;
    let cancel: (() => void) | null = null;

    return create<RunConsoleState>((set, get) => {
        const follow = (mine: number): void => {
            cancel = schedule(async () => {
                cancel = null;
                if (mine !== generation) return;
                if ((await get().pollOnce()) && mine === generation) follow(mine);
            }, RUN_CONSOLE_POLL_MS);
        };

        const stop = (): void => {
            generation++;
            cancel?.();
            cancel = null;
            offset = 0;
            revision = 0;
        };

        return {
            run: null,
            feed: null,
            version: 0,
            live: false,
            error: null,
            shown: readShown(),
            setShown: (shown) => {
                writeShown(shown);
                set({shown});
            },
            open: (scope, run, title) => {
                stop();
                const mine = generation;
                set({run: {scope, runId: run.run_id, title: title ?? run.run_id}, feed: null, version: 0, live: isActiveRun(run), error: null});
                void get()
                    .pollOnce()
                    .then((more) => {
                        if (more && mine === generation) follow(mine);
                    });
            },
            close: () => {
                stop();
                set({run: null, feed: null, version: 0, live: false, error: null});
            },
            pollOnce: async () => {
                const open = get().run;
                const mine = generation;
                if (!open) return false;
                try {
                    const run = await api.getRun(open.scope, open.runId);
                    if (mine !== generation) return false;
                    const live = isActiveRun(run);
                    const artefact = activityArtefact(run);
                    if (!artefact) {
                        // A run that is still going may get its feed yet; one that has ended has none.
                        set({live});
                        return live;
                    }
                    let feed = get().feed;
                    // Stored again from its start (not the append this store expects): read it again.
                    if (artefact.size !== null && artefact.size < offset) {
                        offset = 0;
                        feed = null;
                    }
                    if (artefact.revision !== revision || (artefact.size ?? 0) > offset) {
                        const read = await api.fetchArtefact(open.scope, artefact, {start: offset});
                        if (mine !== generation) return false;
                        feed ??= new ActivityFeed();
                        feed.append(read.bytes);
                        offset += read.bytes.byteLength;
                        revision = artefact.revision;
                        set((s) => ({feed, version: s.version + 1, live, error: null}));
                    } else if (live !== get().live) {
                        set({live});
                    }
                    // More may come while the run is going, the feed is still being written, or it has
                    // not been read to its end.
                    return live || artefact.partial || (artefact.size ?? 0) > offset;
                } catch (err) {
                    if (mine !== generation) return false;
                    set({error: err instanceof Error ? err.message : String(err)});
                    // A run that is going is looked at again: one failed look is not the end of it.
                    return get().live;
                }
            },
        };
    });
}

export const useRunConsoleStore = createRunConsoleStore(simulationsApi);
