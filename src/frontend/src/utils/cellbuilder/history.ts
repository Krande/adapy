/**
 * Pure undo/redo stack mechanics — no zustand, no three.js, node-testable.
 * `T` is an opaque snapshot type; callers decide what a snapshot contains.
 */

export interface HistoryStacks<T> {
    past: T[];
    future: T[];
}

/** Record a new change: push the pre-change snapshot and drop the redo stack. */
export function pushSnapshot<T>(h: HistoryStacks<T>, snapshot: T, limit: number): HistoryStacks<T> {
    return {past: [...h.past, snapshot].slice(-limit), future: []};
}

/** Step back: returns the snapshot to restore and the new stacks, or null when
 * there's nothing to undo. `current` is the present snapshot (pushed to redo). */
export function undoStep<T>(
    h: HistoryStacks<T>,
    current: T,
    limit: number,
): {restored: T; stacks: HistoryStacks<T>} | null {
    if (h.past.length === 0) return null;
    const restored = h.past[h.past.length - 1];
    return {restored, stacks: {past: h.past.slice(0, -1), future: [current, ...h.future].slice(0, limit)}};
}

/** Step forward: mirror of {@link undoStep}. */
export function redoStep<T>(
    h: HistoryStacks<T>,
    current: T,
    limit: number,
): {restored: T; stacks: HistoryStacks<T>} | null {
    if (h.future.length === 0) return null;
    const restored = h.future[0];
    return {restored, stacks: {past: [...h.past, current].slice(-limit), future: h.future.slice(1)}};
}
