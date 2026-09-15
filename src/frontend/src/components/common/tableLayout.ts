// Per-table column layout — which columns are shown, and how wide the user
// dragged them — with the storage format and the reconciliation rules kept
// here, pure and free of React, so they can be tested without a browser.
//
// WHY A KEYED RECORD AND NOT AN ARRAY OF WIDTHS
// The admin storage table's older `useResizableColumns` persists a positional
// `number[]` and throws the whole thing away when `arr.length !== columns.length`.
// That is safe but blunt: adding one column silently resets every user's widths,
// and re-ordering columns silently moves a width onto the wrong one. Here the
// unit of storage is the column KEY, so a release that adds, removes or reorders
// columns keeps the settings that still mean something and quietly forgets the
// ones that do not. That is the property that matters most, because a stored
// layout naming a column that no longer exists must never blank the table.
//
// WHAT IS DELIBERATELY NOT HERE
// * No "hidden by default" flag. It reads as harmless and is not: a column
//   introduced as hidden-by-default in a later release would still show up for
//   every user who already has a stored layout, because their stored `hidden`
//   list is authoritative and cannot name a key that did not exist when it was
//   written. Every column starts visible; the user decides from there.
// * No column re-ordering. Order is the caller's `columns` array. Persisting an
//   order raises the same reconciliation questions again for much less benefit.

/** Storage record. `v` lets a future format change be detected and discarded
 * rather than misread; anything that is not v1 reconciles as "no layout". */
export interface StoredTableLayout {
    v: 1;
    /** Column key → pixel width the user dragged it to. */
    widths: Record<string, number>;
    /** Column keys the user switched off. */
    hidden: string[];
}

/** What a column contributes to the layout. A subset of `DataTableColumn` so
 * this module stays independent of the table component. */
export interface TableLayoutColumnSpec {
    key: string;
    /** Name shown in the column chooser. Falls back to the key. */
    label?: string;
    /** Never hideable. Use it for the column that identifies the row — hiding
     * that one leaves a grid of attributes belonging to nobody. */
    required?: boolean;
}

/** The reconciled, in-memory layout. `hidden` is an array rather than a Set so
 * that the value is structurally comparable in tests and serialisable as-is. */
export interface TableLayoutState {
    widths: Record<string, number>;
    hidden: string[];
}

/** Floor for a dragged column. Below roughly this a header label is unreadable
 * and the grip becomes hard to grab again. Matches the storage table's
 * long-standing MIN_COL_WIDTH so the two features feel the same. */
export const MIN_COLUMN_WIDTH = 48;

/** Ceiling. Not a layout constraint so much as a guard against a stored value
 * from a corrupt write or a dragged-off-screen pointer making a column so wide
 * that the rest of the table is unreachable. */
export const MAX_COLUMN_WIDTH = 2000;

export const EMPTY_TABLE_LAYOUT: TableLayoutState = {widths: {}, hidden: []};

export function clampColumnWidth(px: number): number {
    // Only NaN needs special handling — it fails every comparison, so Math.min
    // and Math.max would both pass it through. ±Infinity clamps correctly on its
    // own, and clamping it is the right answer: a pointer dragged off the far
    // edge should land on the ceiling, not snap back to the floor.
    if (Number.isNaN(px)) return MIN_COLUMN_WIDTH;
    return Math.round(Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTH, px)));
}

/** Read a stored layout. Returns null for anything unusable — missing, not
 * JSON, not v1, wrong shape — so every caller has exactly one "no layout"
 * path instead of a partial object to defend against. */
export function parseStoredLayout(raw: string | null | undefined): StoredTableLayout | null {
    if (!raw) return null;
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return null;
    }
    if (typeof parsed !== "object" || parsed === null) return null;
    const o = parsed as Record<string, unknown>;
    if (o.v !== 1) return null;

    const widths: Record<string, number> = {};
    if (typeof o.widths === "object" && o.widths !== null) {
        for (const [k, v] of Object.entries(o.widths as Record<string, unknown>)) {
            // A non-number (or NaN, or a negative from a bad write) is dropped
            // rather than clamped: clamping would invent a width the user never
            // chose and make it look deliberate.
            if (typeof v === "number" && Number.isFinite(v) && v > 0) widths[k] = Math.round(v);
        }
    }
    const hidden = Array.isArray(o.hidden)
        ? o.hidden.filter((k): k is string => typeof k === "string")
        : [];
    return {v: 1, widths, hidden};
}

export function serializeLayout(state: TableLayoutState): string {
    return JSON.stringify({v: 1, widths: state.widths, hidden: state.hidden} satisfies StoredTableLayout);
}

/**
 * Fold a stored layout onto the columns this build actually has.
 *
 * The rules, in the order they matter:
 *  1. A stored key the current columns do not have is dropped. This is the
 *     whole reason for keying by column: an old layout cannot poison a new one.
 *  2. A current column the stored layout says nothing about takes its defaults
 *     — visible, no width override.
 *  3. A `required` column is never hidden, however the stored layout was
 *     written. A hand-edited localStorage entry cannot remove the identity
 *     column and leave an unreadable grid.
 *  4. If the result would hide EVERY column, nothing is hidden. An empty table
 *     has no header to re-open the chooser from, which is a corner the user
 *     cannot get out of; refusing the state is cheaper than a rescue path.
 *  5. Widths are clamped into [MIN, MAX].
 *
 * Output order follows `specs`, so the result is stable and comparable.
 */
export function reconcileLayout(
    stored: StoredTableLayout | null,
    specs: TableLayoutColumnSpec[],
): TableLayoutState {
    if (!stored) return EMPTY_TABLE_LAYOUT;

    const widths: Record<string, number> = {};
    const hidden: string[] = [];
    for (const spec of specs) {
        const w = stored.widths[spec.key];
        if (typeof w === "number") widths[spec.key] = clampColumnWidth(w);
        if (!spec.required && stored.hidden.includes(spec.key)) hidden.push(spec.key);
    }
    if (specs.length > 0 && hidden.length === specs.length) return {widths, hidden: []};
    return {widths, hidden};
}

/** Keys still on screen, in column order. */
export function visibleColumnKeys(
    specs: TableLayoutColumnSpec[],
    state: TableLayoutState,
): string[] {
    return specs.filter((s) => !state.hidden.includes(s.key)).map((s) => s.key);
}

/**
 * Flip one column's visibility.
 *
 * Showing always works. Hiding is refused when the column is `required`, and
 * when it is the last visible one — same reasoning as rule 4 above. A refused
 * toggle returns the state unchanged (referentially, so React can skip the
 * render) rather than throwing: the chooser disables those entries already, and
 * a click that slipped through should be inert, not fatal.
 */
export function toggleColumn(
    specs: TableLayoutColumnSpec[],
    state: TableLayoutState,
    key: string,
): TableLayoutState {
    const spec = specs.find((s) => s.key === key);
    if (!spec) return state;
    if (state.hidden.includes(key)) {
        return {...state, hidden: state.hidden.filter((k) => k !== key)};
    }
    if (spec.required) return state;
    if (visibleColumnKeys(specs, state).length <= 1) return state;
    return {...state, hidden: [...state.hidden, key]};
}

/** Record a dragged width. */
export function setColumnWidth(
    state: TableLayoutState,
    key: string,
    px: number,
): TableLayoutState {
    const next = clampColumnWidth(px);
    if (state.widths[key] === next) return state;
    return {...state, widths: {...state.widths, [key]: next}};
}

/** Forget a dragged width, returning the column to whatever the caller's own
 * `col` declaration says. The escape hatch for a drag that went wrong; bound to
 * Home on the grip and to a double-click. */
export function clearColumnWidth(state: TableLayoutState, key: string): TableLayoutState {
    if (!(key in state.widths)) return state;
    const widths = {...state.widths};
    delete widths[key];
    return {...state, widths};
}

/** Drop every override. */
export function resetLayout(state: TableLayoutState): TableLayoutState {
    if (state.hidden.length === 0 && Object.keys(state.widths).length === 0) return state;
    return EMPTY_TABLE_LAYOUT;
}

/** True when the user has changed anything — drives the "Reset" entry in the
 * chooser, which is otherwise a button that does nothing. */
export function isLayoutCustomized(state: TableLayoutState): boolean {
    return state.hidden.length > 0 || Object.keys(state.widths).length > 0;
}
