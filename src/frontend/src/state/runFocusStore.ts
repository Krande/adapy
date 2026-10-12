// Which run a list should open on: the toast's "View run" on a sweep or an export entry.
//
// The toast knows a run by its id; the table that shows it lives in a panel the toast does not
// own (a sweep in Admin -> Providers, under its provider's Sweeps; an export in the search panel's
// Export runs). So the toast opens the panel (`viewerPanelStore.openPanel`) and leaves the run id
// here; the list that holds that run expands it, scrolls to it, and clears the focus. Transient on
// purpose -- the toast entry itself is what survives a reload (`sweepTracking.ts` remembers kind,
// id and, for an export, the scope it lives in), and "View run" re-derives the target from it.

import {create} from "zustand";

import type {ViewerPanel} from "./viewerPanelStore";
import type {AdminTabDeepLink} from "./adminPanelStore";

export type RunFocus = {kind: "sweep"; runId: string} | {kind: "export"; runId: string; scope: string};

export interface RunTarget {
    panel: ViewerPanel;
    tab: AdminTabDeepLink | null;
    focus: RunFocus;
}

/** Where a toast entry's run is shown, from its store key (`sweep:<id>` / `export:<id>`). An export
 *  also needs the scope it was run in (its runs are listed per scope); null when it is unknown or
 *  the key is not a run's. */
export function toastRunTarget(key: string, exportScopeOf: (runId: string) => string | null): RunTarget | null {
    const i = key.indexOf(":");
    if (i < 0) return null;
    const kind = key.slice(0, i);
    const runId = key.slice(i + 1);
    if (!runId) return null;
    if (kind === "sweep") return {panel: "admin", tab: "providers", focus: {kind: "sweep", runId}};
    if (kind === "export") {
        const scope = exportScopeOf(runId);
        return scope ? {panel: "search", tab: null, focus: {kind: "export", runId, scope}} : null;
    }
    return null;
}

interface RunFocusState {
    focus: RunFocus | null;
    setFocus: (f: RunFocus | null) => void;
}

export const useRunFocusStore = create<RunFocusState>((set) => ({
    focus: null,
    setFocus: (focus) => set({focus}),
}));
