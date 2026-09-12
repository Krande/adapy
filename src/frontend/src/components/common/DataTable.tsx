import React, {useMemo, useState} from "react";

// A typed table primitive for the admin tabs.
//
// Renders exactly the markup the hand-rolled tables render — `<table>`,
// optional `<colgroup>`, `<thead><tr><th>`, `<tbody><tr><td>` — with every
// class name a prop, so a migrated tab keeps its layout to the pixel. What it
// adds: typed columns (header + cell renderer), a row key, an empty state,
// optional sorting (click a header whose column has `sortValue`), a sticky
// header, and an `overflow-x: auto` wrapper for the tabs that do not already
// sit inside a scroller.

export interface DataTableSort {
    key: string;
    desc: boolean;
}

export interface DataTableColumn<Row> {
    /** Stable identity; doubles as the sort key. */
    key: string;
    header?: React.ReactNode;
    cell: (row: Row, index: number) => React.ReactNode;
    /** `title` attribute of the body cell. */
    title?: (row: Row, index: number) => string | undefined;
    /** Overrides the table-level `headerCellClassName`. */
    headerClassName?: string;
    /** Overrides the table-level `cellClassName`. */
    cellClassName?: string | ((row: Row, index: number) => string);
    /** Header `title` attribute. */
    headerTitle?: string;
    /** Makes the header clickable; rows sort by this value. */
    sortValue?: (row: Row) => string | number | null | undefined;
    /** Direction a fresh sort on this column starts in. Default ascending. */
    sortDefaultDesc?: boolean;
    /** `<col>` attributes. The colgroup renders when any column sets this. */
    col?: {className?: string; style?: React.CSSProperties};
}

export interface DataTableProps<Row> {
    columns: DataTableColumn<Row>[];
    rows: Row[];
    rowKey: (row: Row, index: number) => React.Key;
    /** Rendered after the table when `rows` is empty. */
    emptyState?: React.ReactNode;
    /** Adds `sticky top-0` to the `<thead>`. */
    stickyHeader?: boolean;
    /** Wrap the table in an `overflow-x-auto` div. Default true; pass false
     * when the table already sits inside a scrolling container (a nested
     * scroller would capture a sticky header). */
    wrap?: boolean;
    wrapperClassName?: string;
    /** `<table>` class / style. */
    className?: string;
    style?: React.CSSProperties;
    theadClassName?: string;
    headerRowClassName?: string;
    /** Default `<th>` class; a column's `headerClassName` replaces it. */
    headerCellClassName?: string;
    /** Default `<td>` class; a column's `cellClassName` replaces it. */
    cellClassName?: string;
    tbodyClassName?: string;
    rowClassName?: string | ((row: Row, index: number) => string);
    /** Extra `<tr>` props (click handlers, data attributes). */
    rowProps?: (row: Row, index: number) => React.HTMLAttributes<HTMLTableRowElement>;
    /** Extra rows rendered after a row — an expansion panel. */
    renderAfterRow?: (row: Row, index: number) => React.ReactNode;
    /** Controlled sort. Omit for internal state seeded by `defaultSort`. */
    sort?: DataTableSort | null;
    onSortChange?: (sort: DataTableSort) => void;
    defaultSort?: DataTableSort;
    /** Class of the " ▾"/" ▴" indicator on the sorted header. */
    sortIndicatorClassName?: string;
    "aria-label"?: string;
}

function compareValues(
    a: string | number | null | undefined,
    b: string | number | null | undefined,
    desc: boolean,
): number {
    if (typeof a === "number" && typeof b === "number") return desc ? b - a : a - b;
    const sa = a == null ? "" : String(a);
    const sb = b == null ? "" : String(b);
    return desc ? sb.localeCompare(sa) : sa.localeCompare(sb);
}

function classOf<Row>(
    c: string | ((row: Row, index: number) => string) | undefined,
    row: Row,
    index: number,
): string | undefined {
    return typeof c === "function" ? c(row, index) : c;
}

export function DataTable<Row>(props: DataTableProps<Row>): React.ReactElement {
    const {
        columns,
        rows,
        rowKey,
        emptyState,
        stickyHeader,
        wrap = true,
        wrapperClassName = "overflow-x-auto",
        className,
        style,
        theadClassName,
        headerRowClassName,
        headerCellClassName,
        cellClassName,
        tbodyClassName,
        rowClassName,
        rowProps,
        renderAfterRow,
        defaultSort,
        sortIndicatorClassName = "text-blue-400",
    } = props;

    const [internalSort, setInternalSort] = useState<DataTableSort | null>(defaultSort ?? null);
    const controlled = props.sort !== undefined;
    const sort = controlled ? props.sort ?? null : internalSort;
    const setSort = (next: DataTableSort) => {
        if (!controlled) setInternalSort(next);
        props.onSortChange?.(next);
    };

    const sortedRows = useMemo(() => {
        if (!sort) return rows;
        const col = columns.find((c) => c.key === sort.key);
        if (!col?.sortValue) return rows;
        const sv = col.sortValue;
        const out = [...rows];
        out.sort((a, b) => compareValues(sv(a), sv(b), sort.desc));
        return out;
    }, [rows, columns, sort]);

    const onHeaderClick = (col: DataTableColumn<Row>) => {
        if (!col.sortValue) return;
        if (sort && sort.key === col.key) setSort({key: col.key, desc: !sort.desc});
        else setSort({key: col.key, desc: col.sortDefaultDesc ?? false});
    };

    const hasColgroup = columns.some((c) => c.col);
    const theadClass = [stickyHeader ? "sticky top-0" : "", theadClassName ?? ""]
        .filter(Boolean)
        .join(" ");

    const table = (
        <table className={className} style={style} aria-label={props["aria-label"]}>
            {hasColgroup && (
                <colgroup>
                    {columns.map((c) => (
                        <col key={c.key} className={c.col?.className} style={c.col?.style}/>
                    ))}
                </colgroup>
            )}
            <thead className={theadClass || undefined}>
            <tr className={headerRowClassName}>
                {columns.map((c) => {
                    const sortable = !!c.sortValue;
                    const active = sortable && sort?.key === c.key;
                    return (
                        <th
                            key={c.key}
                            className={c.headerClassName ?? headerCellClassName}
                            title={c.headerTitle}
                            onClick={sortable ? () => onHeaderClick(c) : undefined}
                            aria-sort={active ? (sort?.desc ? "descending" : "ascending") : undefined}
                        >
                            {c.header}
                            {active ? (
                                <span className={sortIndicatorClassName}>{sort?.desc ? " ▾" : " ▴"}</span>
                            ) : null}
                        </th>
                    );
                })}
            </tr>
            </thead>
            <tbody className={tbodyClassName}>
            {sortedRows.map((row, i) => (
                <React.Fragment key={rowKey(row, i)}>
                    <tr className={classOf(rowClassName, row, i)} {...rowProps?.(row, i)}>
                        {columns.map((c) => (
                            <td
                                key={c.key}
                                className={c.cellClassName !== undefined
                                    ? classOf(c.cellClassName, row, i)
                                    : cellClassName}
                                title={c.title?.(row, i)}
                            >
                                {c.cell(row, i)}
                            </td>
                        ))}
                    </tr>
                    {renderAfterRow?.(row, i)}
                </React.Fragment>
            ))}
            </tbody>
        </table>
    );

    const empty = rows.length === 0 ? emptyState : null;
    if (!wrap) {
        return (
            <>
                {table}
                {empty}
            </>
        );
    }
    return (
        <div className={wrapperClassName}>
            {table}
            {empty}
        </div>
    );
}

export default DataTable;
