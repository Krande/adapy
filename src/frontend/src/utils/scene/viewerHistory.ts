/**
 * Recording viewer operations into the undo / redo history (state/viewerHistoryStore.ts).
 *
 * `recordViewerAction(kind, fn)` wraps ONE user action: it notes what is hidden, the selection
 * and any store slices it is given (selection boxes, section planes) before `fn`, runs it, and
 * records what changed as one entry. The visibility, selection, box and plane actions wrap
 * themselves, so every caller -- keyboard, buttons, menus, the tree's eye icons, a gizmo drag --
 * records the same way. Models loaded and removed are entries too (utils/scene/modelHistory.ts).
 *
 * What is selected and what is hidden is the viewport's to say, and to put back: the history asks
 * the viewport on screen for its state before the action, for what the action changed, and hands
 * it one end of the step on undo and redo (viewport/api/viewportState.ts). So the same history works
 * on the three.js viewport (utils/scene/threeViewportState.ts: a diff of the hidden ranges per model
 * and the selection as element ids per model, by names that survive a model being removed and
 * loaded again) and on the three.js-free one (viewport/native/nativeViewportHistory.ts). No three.js
 * in this module. Besides the viewport's state an entry keeps:
 *  - the given store slices before and after (their immutable state, by reference);
 *  - the info panel's name / node id, so undoing a hide brings the selection's name back with it.
 * With no viewport on screen only those are recorded.
 *
 * A wrapped action inside another (show all -> unhide all) records once, as the outer one. While
 * an entry is being undone or redone nothing records.
 */

import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {
    describeViewerAction,
    useViewerHistoryStore,
    type ViewerActionKind,
    type ViewerHistoryEntry,
} from "@/state/viewerHistoryStore";
import {activeViewport} from "@/viewport/api/viewportApi";
import type {ViewportState, ViewportStateParts} from "@/viewport/api/viewportState";

type Info = {name: string | null; selectedNodeId: string | null};

/** A piece of store state an action changes and its undo puts back, e.g. the selection boxes. */
export interface StateSlice<T = unknown> {
    get: () => T;
    set: (value: T) => void;
}

let depth = 0;

/** What a viewport is asked to note: always the selection, and what is hidden only for an action
 *  that can hide or show. */
const SELECTION_ONLY: ViewportStateParts = {selection: true};
const SELECTION_AND_VISIBILITY: ViewportStateParts = {selection: true, visibility: true};

function captureInfo(): Info {
    const {name, selectedNodeId} = useObjectInfoStore.getState();
    return {name, selectedNodeId};
}

function restoreInfo(info: Info): void {
    const s = useObjectInfoStore.getState();
    if (s.name !== info.name) s.setName(info.name);
    if (s.selectedNodeId !== info.selectedNodeId) s.setSelectedNodeId(info.selectedNodeId);
}

/** A selection-only (or slice-only) entry that later actions with the same coalesce key may
 *  still extend. */
let open: {key: string; at: number; entry: ViewerHistoryEntry; extend: (after: ViewportState | null, slices: unknown[]) => void} | null = null;
/** How long after an action one with the same coalesce key still joins it. */
const COALESCE_MS = 1500;

export interface RecordOptions {
    /** Changes with this key, in quick succession, become one entry -- for an action that
     *  re-applies continuously (dragging a selection box's or a section plane's handles). Only an
     *  action that hides or shows nothing joins. */
    readonly coalesce?: string;
    /** Store state the action changes besides visibility and selection. */
    readonly slices?: readonly StateSlice[];
}

/** Run `fn` as one recorded viewer action of `kind`. Returns what `fn` returns. */
export function recordViewerAction<T>(kind: ViewerActionKind, fn: () => T, opts: RecordOptions = {}): T {
    if (depth > 0 || useViewerHistoryStore.getState().applying) return fn();

    const slices = opts.slices ?? [];
    const touchesVisibility =
        kind !== "select" && kind !== "clear-selection" && !kind.startsWith("box-") && !kind.startsWith("plane-") && !kind.startsWith("color-");
    const viewport = activeViewport();
    const before = viewport ? viewport.captureState(touchesVisibility ? SELECTION_AND_VISIBILITY : SELECTION_ONLY) : null;
    const slicesBefore = slices.map((s) => s.get());
    const infoBefore = captureInfo();

    depth++;
    let out: T;
    try {
        out = fn();
    } finally {
        depth--;
    }

    const change = viewport && before ? viewport.stateChange(before) : null;
    const hidden = change?.hidden ?? 0;
    const shown = change?.shown ?? 0;
    const selectionChanged = change?.selection ?? false;
    let slicesAfter = slices.map((s) => s.get());
    const slicesChanged = slicesAfter.some((v, i) => v !== slicesBefore[i]);
    const visibilityChanged = change?.visibility ?? false;
    if (!visibilityChanged && !selectionChanged && !slicesChanged) return out;

    let after = change ? change.after() : null;
    const history = useViewerHistoryStore.getState();
    const now = Date.now();
    if (
        opts.coalesce && !visibilityChanged && open && open.key === opts.coalesce && now - open.at < COALESCE_MS
        && history.future.length === 0 && history.past[history.past.length - 1] === open.entry
    ) {
        open.extend(after, slicesAfter);
        open.at = now;
        return out;
    }

    // The state the action leaves is read when it is first undone, not now: the click path fills
    // in the info panel's name only after a server round-trip, later than this returns.
    let infoAfter: Info | null = null;
    const entry: ViewerHistoryEntry = {
        // A getter: a coalesced entry's selection grows after it is recorded.
        get label() {
            return describeViewerAction(kind, {hidden, shown, selected: after?.selected ?? 0});
        },
        undo: () => {
            infoAfter = captureInfo();
            // Slices first: a restored selection box re-selects what it takes, and the selection
            // put back below must be the last word.
            slices.forEach((s, i) => s.set(slicesBefore[i]));
            if (before) activeViewport()?.restoreState(before);
            restoreInfo(infoBefore);
            requestRender();
        },
        redo: () => {
            slices.forEach((s, i) => s.set(slicesAfter[i]));
            if (after) activeViewport()?.restoreState(after);
            if (infoAfter) restoreInfo(infoAfter);
            requestRender();
        },
    };
    history.record(entry);
    open = opts.coalesce && !visibilityChanged
        ? {
              key: opts.coalesce,
              at: now,
              entry,
              extend: (state, sl) => {
                  after = state;
                  slicesAfter = sl;
              },
          }
        : null;
    return out;
}

/** Undo the latest viewer action. Returns its label, or null when there was none (or one is still
 *  being applied). */
export function undoViewerAction(): string | null {
    return useViewerHistoryStore.getState().undo()?.label ?? null;
}

/** Redo the latest undone viewer action. Returns its label, or null when there was none (or one
 *  is still being applied). */
export function redoViewerAction(): string | null {
    return useViewerHistoryStore.getState().redo()?.label ?? null;
}
