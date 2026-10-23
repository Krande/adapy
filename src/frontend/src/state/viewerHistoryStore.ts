import {create} from "zustand";

/**
 * Undo / redo for viewer operations -- hide, show, isolate, show all, and selection changes
 * (Ctrl+Z / Ctrl+Y).
 *
 * This module holds only the history itself and a few pure helpers, so it can be tested without
 * a scene. What an entry does to the scene lives in its own `undo` / `redo` closures, built by
 * `recordViewerAction` in utils/scene/viewerHistory.ts: one closure pair per USER ACTION (a box
 * selection of 500 objects is one entry, a tree "hide branch" is one entry), never one per store
 * mutation.
 *
 * Entries hold ids and set diffs, never geometry: hiding thousands of ranges costs one array of
 * those ids.
 */

export interface ViewerHistoryEntry {
    /** What the action was, for the hint shown on undo / redo, e.g. "Hide 12 objects". */
    readonly label: string;
    /** Put the scene back as it was before the action. */
    readonly undo: () => void;
    /** Do the action again, from the state `undo` left. */
    readonly redo: () => void;
}

/** How many actions can be undone. The oldest falls off when a new one is recorded. */
export const VIEWER_HISTORY_LIMIT = 100;

interface ViewerHistoryState {
    past: readonly ViewerHistoryEntry[];
    future: readonly ViewerHistoryEntry[];
    /** True while an entry's undo / redo runs: actions it triggers must not record themselves. */
    applying: boolean;
    /** The label of the last entry undone or redone, and which way -- for a brief hint. */
    lastApplied: {readonly direction: "undo" | "redo"; readonly label: string; readonly at: number} | null;
    /** Add a new action. Ignored while applying; clears what could be redone. */
    record: (entry: ViewerHistoryEntry) => void;
    /** Undo the latest action; returns it, or null when there is nothing to undo. */
    undo: () => ViewerHistoryEntry | null;
    /** Redo the latest undone action; returns it, or null when there is nothing to redo. */
    redo: () => ViewerHistoryEntry | null;
    /** Forget everything: the scene the entries point at is gone or replaced. */
    reset: () => void;
}

export const useViewerHistoryStore = create<ViewerHistoryState>()((set, get) => {
    const apply = (direction: "undo" | "redo"): ViewerHistoryEntry | null => {
        const s = get();
        if (s.applying) return null;
        const from = direction === "undo" ? s.past : s.future;
        const entry = from[from.length - 1];
        if (!entry) return null;
        set({applying: true});
        try {
            if (direction === "undo") entry.undo();
            else entry.redo();
        } finally {
            const now = get();
            const rest = (direction === "undo" ? now.past : now.future).slice(0, -1);
            const other = [...(direction === "undo" ? now.future : now.past), entry].slice(-VIEWER_HISTORY_LIMIT);
            set({
                applying: false,
                past: direction === "undo" ? rest : other,
                future: direction === "undo" ? other : rest,
                lastApplied: {direction, label: entry.label, at: Date.now()},
            });
        }
        return entry;
    };
    return {
        past: [],
        future: [],
        applying: false,
        lastApplied: null,
        record: (entry) => {
            if (get().applying) return;
            set((s) => ({past: [...s.past, entry].slice(-VIEWER_HISTORY_LIMIT), future: []}));
        },
        undo: () => apply("undo"),
        redo: () => apply("redo"),
        reset: () => set({past: [], future: [], lastApplied: null}),
    };
});

// ---------------------------------------------------------------------------------------------
// Pure helpers used to build entries.

/** What `before` -> `after` added and removed. */
export function diffSets(before: ReadonlySet<string>, after: ReadonlySet<string>): {added: string[]; removed: string[]} {
    const added: string[] = [];
    const removed: string[] = [];
    for (const id of after) if (!before.has(id)) added.push(id);
    for (const id of before) if (!after.has(id)) removed.push(id);
    return {added, removed};
}

/** Whether two selections (object -> range ids) hold the same ranges. */
export function sameSelection<K>(a: ReadonlyMap<K, ReadonlySet<string>>, b: ReadonlyMap<K, ReadonlySet<string>>): boolean {
    if (a === b) return true;
    const nonEmpty = (m: ReadonlyMap<K, ReadonlySet<string>>) => [...m].filter(([, ids]) => ids.size > 0);
    const ea = nonEmpty(a);
    const eb = nonEmpty(b);
    if (ea.length !== eb.length) return false;
    for (const [k, ids] of ea) {
        const other = b.get(k);
        if (!other || other.size !== ids.size) return false;
        for (const id of ids) if (!other.has(id)) return false;
    }
    return true;
}

export type ViewerActionKind = "hide" | "hide-others" | "show" | "isolate" | "show-all" | "select" | "clear-selection";

const objects = (n: number) => `${n} object${n === 1 ? "" : "s"}`;

/** The label an action gets in the history, from what it changed. */
export function describeViewerAction(
    kind: ViewerActionKind,
    changed: {readonly hidden: number; readonly shown: number; readonly selected: number},
): string {
    switch (kind) {
        case "hide":
            return `hide ${objects(changed.hidden)}`;
        case "hide-others":
            return `hide ${objects(changed.hidden)} outside the selection`;
        case "show":
            return `show ${objects(changed.shown)}`;
        case "isolate":
            return changed.shown > 0
                ? `isolate (hide ${objects(changed.hidden)}, show ${changed.shown})`
                : `isolate (hide ${objects(changed.hidden)})`;
        case "show-all":
            return `show all (${objects(changed.shown)})`;
        case "select":
            return changed.selected > 0 ? `select ${objects(changed.selected)}` : "clear selection";
        case "clear-selection":
            return "clear selection";
    }
}
