import React, {useEffect, useMemo, useRef, useState} from "react";
import type {DataTableColumn} from "./DataTable";
import {
    EMPTY_TABLE_LAYOUT,
    MIN_COLUMN_WIDTH,
    clampColumnWidth,
    type TableLayoutColumnSpec,
    type TableLayoutState,
    clearColumnWidth,
    isLayoutCustomized,
    parseStoredLayout,
    reconcileLayout,
    resetLayout,
    serializeLayout,
    setColumnWidth,
    toggleColumn,
} from "./tableLayout";

// Drag-resizable columns and a column chooser for any `DataTable`, added as a
// wrapper rather than as props on the table itself.
//
// WHY A WRAPPER. Eighteen call sites render `DataTable`. Anything put INSIDE it
// is inherited by all eighteen, and most of them — a five-row metrics table, a
// scope picker in a modal — want neither a drag grip nor a kebab. So the whole
// feature is expressed as a transform over the caller's `DataTableColumn[]`:
// hiding a column is "do not pass it", resizing it is "put a width on its
// `col`", and the grip is just more `header` content. A table that does not
// call this hook renders exactly the markup it rendered before.
//
// WHERE THE MENU GOES. Not in the header row. `stickyHeader` makes `<thead>`
// `sticky top-0`, which pins it VERTICALLY inside the scroller and lets it
// scroll horizontally with the body — measured, not assumed. On the two tables
// that opt in here (1200px and 1260px minimum widths) the right-hand end of the
// header row is off-screen until you scroll right, so a kebab living there
// would be hidden exactly when the table is too wide, which is the one case the
// feature exists for. A header-row kebab would also need a seventh `<th>`, and
// therefore a seventh `<td>` in every row and a bump to every hard-coded
// `colSpan`. The hook returns a `menu` node instead and the caller puts it in
// its own non-scrolling chrome.
//
// PERSISTENCE. `localStorage`, one entry per table under a caller-chosen key.
// Per browser profile, so per-user in practice, and per-table. Deliberately not
// server-side: the admin API has no per-user preference store and column widths
// do not justify inventing one, nor a migration. See `tableLayout.ts` for what
// happens when a stored layout and the current columns disagree.
//
// NOT MIGRATED. `adminStorage/useResizableColumns` predates this and still
// drives the storage table. It models something this hook does not — the table
// width IS the sum of its column widths, with numeric defaults declared up
// front — and it works; folding the two together is worth doing on its own, not
// as a rider on this change.

export interface UseTableLayoutOptions<Row> {
    /** `localStorage` key. Namespace it per table, e.g. "adapy.admin.members". */
    storageKey: string;
    /** The caller's columns, in display order. */
    columns: DataTableColumn<Row>[];
    /** The table-level `headerCellClassName`, if any. Needed because a column's
     * own `headerClassName` REPLACES it, and the grip needs the header cell to
     * be a positioned ancestor — so the hook has to reconstruct the class it is
     * about to override. */
    headerCellClassName?: string;
    /** Set false for a table where a pixel width is only advisory — a
     * `table-auto` layout may overrule a `<col>` width to fit content, so a grip
     * there promises precision it cannot deliver. The chooser still works. */
    resizable?: boolean;
    /** Name used in the menu button's tooltip and aria-label. */
    label?: string;
}

export interface UseTableLayoutResult<Row> {
    /** Pass to `<DataTable columns={…}>` in place of the caller's array. */
    columns: DataTableColumn<Row>[];
    /** The three-dot menu. Render it in chrome that does NOT scroll with the
     * table body. */
    menu: React.ReactElement;
    /** Pass to `<DataTable style={…}>`. Defined only once every visible column
     * has a width — see `seedWidths` for why that is all-or-nothing. */
    tableStyle: React.CSSProperties | undefined;
    hidden: string[];
    resetColumns: () => void;
}

const specOf = <Row, >(c: DataTableColumn<Row>): TableLayoutColumnSpec => ({
    key: c.key,
    label: c.label ?? (typeof c.header === "string" ? c.header : undefined) ?? c.key,
    required: c.required,
});

export function useTableLayout<Row>(
    opts: UseTableLayoutOptions<Row>,
): UseTableLayoutResult<Row> {
    const {storageKey, columns, headerCellClassName, resizable = true, label} = opts;

    const specs = useMemo(() => columns.map(specOf), [columns]);

    // Read once, on mount, and reconcile against the columns this build has.
    // `useState`'s initializer rather than an effect: a one-frame flash of the
    // default layout before the stored one lands is exactly the "worse than
    // none" experience persistence is supposed to avoid.
    const [state, setState] = useState<TableLayoutState>(() => {
        try {
            return reconcileLayout(parseStoredLayout(localStorage.getItem(storageKey)), specs);
        } catch {
            // Storage can throw outright (disabled cookies, private modes).
            return EMPTY_TABLE_LAYOUT;
        }
    });

    useEffect(() => {
        try {
            if (isLayoutCustomized(state)) localStorage.setItem(storageKey, serializeLayout(state));
            else localStorage.removeItem(storageKey);
        } catch {
            /* storage full or blocked — the layout still works for this session */
        }
    }, [state, storageKey]);

    // Live drag. A ref, not state: it changes on every pointermove and none of
    // it belongs in a render.
    const drag = useRef<{key: string; startX: number; startW: number} | null>(null);

    /**
     * Return a state in which every visible column has a width, seeding the ones
     * that do not from the header cells as they are rendered right now.
     *
     * All-or-nothing, because of how `table-fixed` composes with `w-full` and a
     * `min-w-[…]` floor. When the declared `<col>` widths add up to less than the
     * table, the browser scales them ALL up by the same factor to fill it — a
     * 96px column renders at 107.5px in a 1200px table whose columns declare
     * 1072px. Store a width measured under that scaling and the next render
     * scales it AGAIN: the column overshoots where the pointer was released, and
     * every further drag compounds the error.
     *
     * Seeding every column at once and then pinning the table to their sum (see
     * `tableStyle`) takes the scaling out of the loop: from the first drag on,
     * declared width and rendered width are the same number. And because the
     * drag basis afterwards is the STORED width rather than a re-measurement,
     * there is no path back into compounding.
     *
     * Scaling survives in exactly one place: a table whose `min-w-[…]` floor
     * exceeds the sum the user dragged to. The floor wins — it is the caller
     * saying the table is unreadable below that — and the columns scale up
     * proportionally inside it, keeping the ratios that were dragged if not the
     * pixels. That is a compromise, not a bug, and it does not compound.
     */
    const seedWidths = (prev: TableLayoutState, grip: HTMLElement): TableLayoutState => {
        const row = grip.closest("th")?.parentElement;
        if (!row) return prev;
        const cells = Array.from(row.children) as HTMLElement[];
        // The header row holds exactly the visible columns, in order. If it does
        // not, something upstream renders its own header and measuring would
        // assign widths to the wrong columns — leave the layout alone.
        const visible = specs.filter((s) => !prev.hidden.includes(s.key));
        if (cells.length !== visible.length) return prev;
        let changed = false;
        const widths = {...prev.widths};
        visible.forEach((s, i) => {
            if (widths[s.key] === undefined) {
                widths[s.key] = clampColumnWidth(cells[i].offsetWidth);
                changed = true;
            }
        });
        return changed ? {...prev, widths} : prev;
    };

    const gripHandlers = (key: string) => ({
        onPointerDown: (e: React.PointerEvent<HTMLElement>) => {
            // Pointer events, not mouse events, so touch and pen drags work too;
            // pointer capture so the drag survives the pointer leaving a 6px
            // grip, which it does immediately.
            if (e.pointerType === "mouse" && e.button !== 0) return;
            e.preventDefault();
            e.stopPropagation(); // a sortable header must not sort on a resize
            e.currentTarget.setPointerCapture?.(e.pointerId);
            const seeded = seedWidths(state, e.currentTarget);
            if (seeded !== state) setState(seeded);
            drag.current = {
                key,
                startX: e.clientX,
                startW: seeded.widths[key] ?? MIN_COLUMN_WIDTH,
            };
        },
        onPointerMove: (e: React.PointerEvent<HTMLElement>) => {
            const d = drag.current;
            if (!d) return;
            setState((prev) => setColumnWidth(prev, d.key, d.startW + (e.clientX - d.startX)));
        },
        onPointerUp: () => {
            drag.current = null;
        },
        onPointerCancel: () => {
            drag.current = null;
        },
        onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => {
            // A grip that answers only a pointer is a control some people simply
            // cannot use, so the same resize is on the arrow keys: ←/→ step,
            // Shift takes a bigger bite, Home hands the column back to whatever
            // the caller declared. Everything else falls through to the browser.
            let delta = 0;
            if (e.key === "ArrowLeft") delta = e.shiftKey ? -64 : -16;
            else if (e.key === "ArrowRight") delta = e.shiftKey ? 64 : 16;
            else if (e.key === "Home") {
                e.preventDefault();
                setState((prev) => clearColumnWidth(prev, key));
                return;
            } else return;
            e.preventDefault();
            e.stopPropagation();
            const seeded = seedWidths(state, e.currentTarget);
            setState(setColumnWidth(seeded, key, (seeded.widths[key] ?? MIN_COLUMN_WIDTH) + delta));
        },
        onDoubleClick: () => setState((prev) => clearColumnWidth(prev, key)),
    });

    const visibleColumns = useMemo(
        () => columns.filter((c) => !state.hidden.includes(c.key)),
        [columns, state.hidden],
    );

    // Not memoized: the handlers above close over `state`, so a memo would need
    // `state` as a dependency and would be rebuilt on every drag frame anyway.
    const laidOutColumns = visibleColumns.map((c) => {
        const width = state.widths[c.key];
        // An inline width beats a Tailwind `w-*` class on the same <col>, so a
        // caller's declared width stays as the default and the dragged one takes
        // over without either having to be deleted.
        const col = width === undefined
            ? c.col
            : {...c.col, style: {...c.col?.style, width: `${width}px`}};
        if (!resizable) return col === c.col ? c : {...c, col};
        const base = c.headerClassName ?? headerCellClassName ?? "";
        return {
            ...c,
            col,
            // `relative` so the grip can sit on the cell's right border.
            headerClassName: base.includes("relative") ? base : `${base} relative`.trim(),
            header: (
                <>
                    {c.header}
                    <ColumnResizeGrip
                        label={c.label ?? (typeof c.header === "string" ? c.header : c.key)}
                        {...gripHandlers(c.key)}
                    />
                </>
            ),
        };
    });

    // Only pin the table once every visible column has a width; before the first
    // drag the caller's own `className` is left to do what it always did.
    const tableStyle = useMemo<React.CSSProperties | undefined>(() => {
        let total = 0;
        for (const c of visibleColumns) {
            const w = state.widths[c.key];
            if (w === undefined) return undefined;
            total += w;
        }
        return total > 0 ? {width: total} : undefined;
    }, [visibleColumns, state.widths]);

    const resetColumns = () => setState((prev) => resetLayout(prev));

    const menu = (
        <ColumnChooserMenu
            label={label}
            specs={specs}
            hidden={state.hidden}
            customized={isLayoutCustomized(state)}
            onToggle={(key) => setState((prev) => toggleColumn(specs, prev, key))}
            onReset={resetColumns}
        />
    );

    return {columns: laidOutColumns, menu, tableStyle, hidden: state.hidden, resetColumns};
}

/** The drag handle on a header cell's right border. `separator` with an
 * orientation is the role a resizer between two regions has; it is focusable so
 * the keyboard path above is reachable by tabbing. */
const ColumnResizeGrip: React.FC<{
    label: string;
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => void;
    onPointerMove: (e: React.PointerEvent<HTMLElement>) => void;
    onPointerUp: () => void;
    onPointerCancel: () => void;
    onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => void;
    onDoubleClick: () => void;
}> = (p) => (
    <span
        role="separator"
        aria-orientation="vertical"
        aria-label={`Resize ${p.label} column`}
        title="Drag, or focus and use ←/→ (Shift for bigger steps, Home to reset)"
        tabIndex={0}
        // `touch-none` stops the browser claiming a horizontal drag as a scroll
        // gesture before the pointer handlers see it.
        className="absolute top-0 right-0 z-10 h-full w-1.5 cursor-col-resize touch-none
                   hover:bg-blue-500/60 focus:bg-blue-500/60 focus:outline-hidden"
        onPointerDown={p.onPointerDown}
        onPointerMove={p.onPointerMove}
        onPointerUp={p.onPointerUp}
        onPointerCancel={p.onPointerCancel}
        onKeyDown={p.onKeyDown}
        onDoubleClick={p.onDoubleClick}
        onClick={(e) => e.stopPropagation()}
    />
);

/** The three-dot button and its panel of column checkboxes. */
const ColumnChooserMenu: React.FC<{
    label?: string;
    specs: TableLayoutColumnSpec[];
    hidden: string[];
    customized: boolean;
    onToggle: (key: string) => void;
    onReset: () => void;
}> = ({label, specs, hidden, customized, onToggle, onReset}) => {
    const [open, setOpen] = useState(false);
    const root = useRef<HTMLDivElement | null>(null);
    const button = useRef<HTMLButtonElement | null>(null);

    useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== "Escape") return;
            setOpen(false);
            button.current?.focus(); // Escape must not strand focus in a closed panel
        };
        const onDown = (e: MouseEvent) => {
            if (!root.current?.contains(e.target as Node)) setOpen(false);
        };
        window.addEventListener("keydown", onKey);
        window.addEventListener("mousedown", onDown);
        return () => {
            window.removeEventListener("keydown", onKey);
            window.removeEventListener("mousedown", onDown);
        };
    }, [open]);

    const shownCount = specs.length - hidden.length;
    const title = label ? `Columns — ${label}` : "Columns";

    return (
        <div className="relative" ref={root}>
            <button
                ref={button}
                type="button"
                className="px-2 py-1 rounded-sm text-gray-300 hover:text-white hover:bg-gray-700
                           text-base leading-none no-drag"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-label={title}
                title={`${title} (${shownCount} of ${specs.length} shown)`}
                onClick={() => setOpen((v) => !v)}
            >
                ⋮
            </button>
            {open && (
                <div
                    role="menu"
                    aria-label={title}
                    className="absolute right-0 top-full mt-1 z-50 min-w-[13rem] rounded-sm border
                               border-gray-600 bg-gray-900 shadow-lg py-1 text-xs"
                >
                    <div className="px-3 py-1 text-[11px] uppercase tracking-wide text-gray-500">
                        Columns
                    </div>
                    {specs.map((s) => {
                        const visible = !hidden.includes(s.key);
                        // The last visible column cannot be switched off either;
                        // an empty table has no header left to re-open this from.
                        const locked = s.required || (visible && shownCount <= 1);
                        return (
                            <label
                                key={s.key}
                                className={
                                    "flex items-center gap-2 px-3 py-1.5 " +
                                    (locked ? "text-gray-500" : "text-gray-200 hover:bg-gray-800 cursor-pointer")
                                }
                                title={
                                    s.required
                                        ? "Always shown — it is what identifies the row"
                                        : locked
                                            ? "At least one column has to stay"
                                            : undefined
                                }
                            >
                                <input
                                    type="checkbox"
                                    className="accent-blue-500"
                                    checked={visible}
                                    disabled={locked}
                                    onChange={() => onToggle(s.key)}
                                />
                                <span className="truncate">{s.label || s.key}</span>
                            </label>
                        );
                    })}
                    <button
                        type="button"
                        role="menuitem"
                        className="mt-1 w-full text-left px-3 py-1.5 border-t border-gray-700
                                   text-gray-300 hover:bg-gray-800 disabled:text-gray-600
                                   disabled:hover:bg-transparent"
                        disabled={!customized}
                        onClick={() => {
                            onReset();
                            setOpen(false);
                        }}
                    >
                        Reset columns
                    </button>
                </div>
            )}
        </div>
    );
};

export default useTableLayout;
